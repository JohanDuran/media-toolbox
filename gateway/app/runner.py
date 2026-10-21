"""The job queue: schedules jobs onto workers, follows them, and records results.

Rules:
  - each worker runs one job at a time;
  - GPU workers share one GPU lock (8 GB of VRAM fits one model comfortably), and
    before a GPU job starts, the other GPU workers are asked to unload their models;
  - jobs wait in the queue while their worker is down, and start once it is back.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import time
import traceback

import httpx

from .config import CONTAINER_HOME, HOST_HOME, WORKERS
from .db import FINISHED, JobDB
from .paths import results_to_windows, windows
from .tools import TOOLS, prepare


_CONTAINER_PATH = re.compile(re.escape(CONTAINER_HOME) + r"(/[^\s'\"]*)")


def _host_paths(line: str) -> str:
    """Show Windows paths in worker logs instead of container paths."""
    return _CONTAINER_PATH.sub(lambda m: windows(HOST_HOME + m.group(1)), line)


class LocalCtx:
    def __init__(self):
        self.lines: list[str] = []

    def log(self, msg: str) -> None:
        self.lines.append(f"{time.strftime('%H:%M:%S')} {msg}")


class Runner:
    def __init__(self, db: JobDB):
        self.db = db
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=30.0))
        self.wake = asyncio.Event()
        self.running: dict[str, str] = {}  # worker -> job id
        self.done_events: dict[str, asyncio.Event] = {}
        self.health: dict[str, dict] = {name: {"up": False, "checked": None} for name in WORKERS}
        self.gpu: dict | None = None

    # --- submitting ------------------------------------------------------------------

    def submit(self, tool_name: str, params: dict, source: str = "api") -> dict:
        tool = TOOLS.get(tool_name)
        if tool is None:
            raise ValueError(f"unknown tool {tool_name!r}; have {sorted(TOOLS)}")
        cparams = prepare(tool, dict(params))
        job = self.db.create(tool.name, tool.worker, tool.gpu, params, cparams, source)
        self.done_events[job["id"]] = asyncio.Event()
        self.wake.set()
        return job

    async def wait(self, job_id: str, timeout: float) -> dict:
        job = self.db.get(job_id)
        if job is None:
            raise ValueError(f"unknown job {job_id}")
        if job["status"] not in FINISHED:
            ev = self.done_events.setdefault(job_id, asyncio.Event())
            try:
                await asyncio.wait_for(ev.wait(), timeout)
            except asyncio.TimeoutError:
                pass
        return self.db.get(job_id)

    async def cancel(self, job_id: str) -> dict:
        job = self.db.get(job_id)
        if job is None:
            raise ValueError(f"unknown job {job_id}")
        if job["status"] == "queued":
            self._finish(job_id, "cancelled", error="cancelled before it started")
        elif job["status"] == "running" and job["worker"] in WORKERS:
            await self.http.post(f"{WORKERS[job['worker']]['url']}/jobs/{job_id}/cancel")
            self.db.append_logs(job_id, [f"{time.strftime('%H:%M:%S')} cancel requested"])
        return self.db.get(job_id)

    # --- background loops ------------------------------------------------------------

    async def schedule_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self.wake.wait(), 2.0)
            except asyncio.TimeoutError:
                pass
            self.wake.clear()
            try:
                self._schedule()
            except Exception:
                traceback.print_exc()

    def _schedule(self) -> None:
        gpu_busy = any(WORKERS.get(w, {}).get("gpu") for w in self.running)
        for job in self.db.queued():
            worker = job["worker"]
            if worker in self.running:
                continue
            if worker != "gateway" and not self.health[worker].get("up"):
                continue  # waits until the worker is back
            if job["gpu"] and gpu_busy:
                continue
            self.running[worker] = job["id"]
            if job["gpu"]:
                gpu_busy = True
            self.db.update(job["id"], status="running", started=time.time())
            asyncio.create_task(self._run(job))

    async def health_loop(self) -> None:
        while True:
            await asyncio.gather(*(self._check(name) for name in WORKERS), self._gpu_stats(),
                                 return_exceptions=True)
            self.wake.set()
            await asyncio.sleep(3)

    async def _check(self, name: str) -> None:
        url = WORKERS[name]["url"]
        h = self.health[name]
        try:
            r = await self.http.get(f"{url}/health", timeout=3)
            r.raise_for_status()
            h.update(r.json(), up=True, error=None, checked=time.time())
            if "info" not in h or h.get("info_age", 0) + 30 < time.time():
                i = await self.http.get(f"{url}/info", timeout=10)
                h.update(info=i.json(), info_age=time.time())
        except (httpx.ConnectError, httpx.ConnectTimeout):
            h.update(up=False, error="container not running (or still starting)", checked=time.time())
            h.pop("info", None)
        except Exception as e:
            h.update(up=False, error=f"{type(e).__name__}: {e}", checked=time.time())
            h.pop("info", None)

    async def _gpu_stats(self) -> None:
        def query():
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
                 "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
            name, used, total, util, temp = [x.strip() for x in out.stdout.strip().split(",")]
            return {"name": name, "memory_used_mb": int(used), "memory_total_mb": int(total),
                    "utilization": int(util), "temperature_c": int(temp)}
        try:
            self.gpu = await asyncio.to_thread(query)
        except Exception as e:
            self.gpu = {"error": str(e)}

    # --- running one job -------------------------------------------------------------

    def _finish(self, job_id: str, status: str, result=None, error: str | None = None) -> None:
        self.db.update(job_id, status=status, result=result, error=error, finished=time.time())
        ev = self.done_events.pop(job_id, None)
        if ev:
            ev.set()

    async def _run(self, job: dict) -> None:
        job_id, worker = job["id"], job["worker"]
        tool = TOOLS[job["tool"]]
        try:
            if worker == "gateway":
                ctx = LocalCtx()
                try:
                    result = await asyncio.to_thread(tool.local, job["cparams"], ctx.log)
                finally:
                    self.db.append_logs(job_id, ctx.lines)
                self._finish(job_id, "done", result=results_to_windows(result))
                return

            if job["gpu"]:
                await self._make_vram_room(worker, job_id)
            url = WORKERS[worker]["url"]
            r = await self.http.post(f"{url}/run",
                                     json={"job_id": job_id, "action": tool.action, "params": job["cparams"]})
            if r.status_code >= 400:
                raise RuntimeError(f"{worker} refused the job: {r.text}")

            seen, misses = 0, 0
            while True:
                await asyncio.sleep(1)
                try:
                    s = (await self.http.get(f"{url}/jobs/{job_id}", params={"since": seen})).json()
                    misses = 0
                except Exception:
                    misses += 1
                    if misses >= 30:
                        raise RuntimeError(f"{worker} stopped responding")
                    continue
                if s.get("logs"):
                    self.db.append_logs(job_id, [_host_paths(line) for line in s["logs"]])
                    seen = s["log_count"]
                if s.get("progress") is not None:
                    self.db.update(job_id, progress=s["progress"])
                if s["status"] == "running":
                    continue
                if s["status"] == "done":
                    result = s["result"]
                    if tool.finish:
                        result = tool.finish(result, job["cparams"])
                    self._finish(job_id, "done", result=results_to_windows(result))
                else:
                    self._finish(job_id, s["status"], error=s.get("error") or s["status"])
                return
        except Exception as e:
            self.db.append_logs(job_id, [f"{time.strftime('%H:%M:%S')} gateway: {type(e).__name__}: {e}"])
            self._finish(job_id, "failed", error=f"{type(e).__name__}: {e}")
        finally:
            self.running.pop(worker, None)
            self.wake.set()

    async def _make_vram_room(self, worker: str, job_id: str) -> None:
        for other, cfg in WORKERS.items():
            if other == worker or not cfg["gpu"] or not self.health[other].get("model_loaded"):
                continue
            try:
                await self.http.post(f"{cfg['url']}/unload", timeout=60)
                self.health[other]["model_loaded"] = False
                self.db.append_logs(job_id, [f"{time.strftime('%H:%M:%S')} gateway: unloaded {other} to free VRAM"])
            except Exception as e:
                self.db.append_logs(job_id, [f"{time.strftime('%H:%M:%S')} gateway: couldn't unload {other}: {e}"])

    def state(self) -> dict:
        workers = []
        for name, cfg in WORKERS.items():
            h = self.health[name]
            workers.append({"name": name, "title": cfg["title"], "gpu": cfg["gpu"], "up": h.get("up", False),
                            "busy": name in self.running, "job_id": self.running.get(name),
                            "model_loaded": h.get("model_loaded", False), "error": h.get("error"),
                            "info": h.get("info"), "checked": h.get("checked")})
        return {"workers": workers, "gpu": self.gpu, "counts": self.db.counts()}
