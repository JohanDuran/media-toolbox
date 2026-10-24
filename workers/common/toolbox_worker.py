"""The contract every toolbox worker follows, so the gateway can treat them all alike.

A worker is a small FastAPI app wrapping one tool. It runs one job at a time in a
background thread and exposes:

  GET  /health            up?, busy?, model loaded?
  GET  /info              name, version, actions, device / VRAM
  POST /run               {job_id, action, params} -> starts the job (409 if busy)
  GET  /jobs/{id}?since=N status, progress, log lines from N on, result or error
  POST /jobs/{id}/cancel  asks the running job to stop at its next checkpoint
  POST /unload            frees the model (the gateway calls this to make VRAM room)

Paths in params and results are container paths (/host/...); the gateway converts
them from and to Windows paths.
"""

from __future__ import annotations

import threading
import time
import traceback
from collections import OrderedDict
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class Cancelled(Exception):
    pass


class JobContext:
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.logs: list[str] = []
        self.progress: float | None = None
        self.cancel_requested = False
        self.status = "running"
        self.result: Any = None
        self.error: str | None = None

    def log(self, msg: str) -> None:
        for line in str(msg).splitlines() or [""]:
            self.logs.append(f"{time.strftime('%H:%M:%S')} {line}")
            print(f"[{self.job_id}] {line}", flush=True)

    def set_progress(self, p: float) -> None:
        self.progress = max(0.0, min(1.0, float(p)))

    def check_cancel(self) -> None:
        if self.cancel_requested:
            raise Cancelled()


Handler = Callable[[dict, JobContext], Any]


class RunRequest(BaseModel):
    job_id: str
    action: str
    params: dict = {}


def create_app(
    name: str,
    version: str,
    handlers: dict[str, Handler],
    *,
    info: Callable[[], dict] | None = None,
    loaded: Callable[[], bool] | None = None,
    unload: Callable[[], None] | None = None,
) -> FastAPI:
    app = FastAPI(title=f"toolbox worker: {name}")
    jobs: OrderedDict[str, JobContext] = OrderedDict()
    lock = threading.Lock()
    state = {"current": None}

    def run(ctx: JobContext, action: str, params: dict) -> None:
        try:
            ctx.result = handlers[action](params, ctx)
            ctx.status = "done"
        except Cancelled:
            ctx.status = "cancelled"
            ctx.log("cancelled")
        except Exception as e:  # report every failure back to the gateway
            ctx.status = "failed"
            ctx.error = f"{type(e).__name__}: {e}"
            ctx.log(traceback.format_exc())
        finally:
            state["current"] = None

    @app.get("/health")
    def health():
        return {"ok": True, "busy": state["current"] is not None, "job_id": state["current"],
                "model_loaded": bool(loaded and loaded())}

    @app.get("/info")
    def get_info():
        data = {"name": name, "version": version, "actions": sorted(handlers),
                "model_loaded": bool(loaded and loaded())}
        if info:
            try:
                data.update(info())
            except Exception as e:
                data["info_error"] = str(e)
        return data

    @app.post("/run")
    def start(req: RunRequest):
        if req.action not in handlers:
            raise HTTPException(400, f"unknown action {req.action!r}; have {sorted(handlers)}")
        with lock:
            if state["current"] is not None:
                raise HTTPException(409, f"busy with job {state['current']}")
            ctx = JobContext(req.job_id)
            jobs[req.job_id] = ctx
            while len(jobs) > 50:
                jobs.popitem(last=False)
            state["current"] = req.job_id
        threading.Thread(target=run, args=(ctx, req.action, req.params), daemon=True).start()
        return {"started": req.job_id}

    @app.get("/jobs/{job_id}")
    def job(job_id: str, since: int = 0):
        ctx = jobs.get(job_id)
        if ctx is None:
            raise HTTPException(404, "unknown job")
        return {"status": ctx.status, "progress": ctx.progress, "logs": ctx.logs[since:],
                "log_count": len(ctx.logs), "result": ctx.result, "error": ctx.error}

    @app.post("/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        ctx = jobs.get(job_id)
        if ctx is None:
            raise HTTPException(404, "unknown job")
        ctx.cancel_requested = True
        return {"cancel_requested": True}

    @app.get("/gpu")
    def gpu():
        """Whole-GPU figures from nvidia-smi, for the dashboard (the gateway itself has no GPU)."""
        try:
            return nvidia_smi()
        except FileNotFoundError:
            raise HTTPException(404, "nvidia-smi not available in this worker")

    @app.post("/unload")
    def do_unload():
        if state["current"] is not None:
            raise HTTPException(409, "busy")
        if unload:
            unload()
        return {"model_loaded": bool(loaded and loaded())}

    return app


def nvidia_smi() -> dict:
    import subprocess

    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
         "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
    name, used, total, util, temp = [x.strip() for x in out.stdout.strip().splitlines()[0].split(",")]
    return {"name": name, "memory_used_mb": int(used), "memory_total_mb": int(total),
            "utilization": int(util), "temperature_c": int(temp)}


def cuda_info() -> dict:
    """Device and VRAM figures for /info (torch workers only)."""
    import torch

    if not torch.cuda.is_available():
        return {"device": "cpu"}
    free, total = torch.cuda.mem_get_info()
    return {
        "device": torch.cuda.get_device_name(0),
        "torch": torch.__version__,
        "vram_used_by_worker_mb": round(torch.cuda.memory_reserved() / 2**20),
        "vram_free_mb": round(free / 2**20),
        "vram_total_mb": round(total / 2**20),
    }


def free_cuda() -> None:
    import gc

    import torch

    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
