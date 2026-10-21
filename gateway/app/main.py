"""Gateway: dashboard (/), REST API (/api) and MCP endpoint (/mcp) on one port."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import mimetypes
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import library
from .config import HOST_HOME, OUTPUTS_DIR, PROTECTED_DIRS, PUBLIC_URL, SFX_LIBRARY, WEB_DIR, WORKERS
from .db import JobDB
from .mcp_tools import build_mcp
from .paths import PathError, to_container, windows
from .runner import Runner
from .tools import TOOLS

logging.getLogger("httpx").setLevel(logging.WARNING)  # health checks every 3 s would flood the log

db = JobDB()
runner = Runner(db)
mcp = build_mcp(runner)
mcp_app = mcp.streamable_http_app()


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    tasks = [asyncio.create_task(runner.schedule_loop()), asyncio.create_task(runner.health_loop())]
    async with mcp.session_manager.run():
        yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="media-toolbox", lifespan=lifespan)


def bad_request(e: Exception) -> HTTPException:
    return HTTPException(400, str(e))


@app.get("/api/state")
def state():
    return {**runner.state(), "config": {
        "host_home": windows(HOST_HOME), "outputs_dir": windows(OUTPUTS_DIR), "sfx_library": windows(SFX_LIBRARY),
        "protected_dirs": [windows(d) for d in PROTECTED_DIRS], "mcp_url": f"{PUBLIC_URL}/mcp"}}


@app.get("/api/tools")
def tools():
    return [t.public() for t in TOOLS.values()]


@app.get("/api/jobs")
def jobs(limit: int = 50, status: str | None = None, tool: str | None = None):
    return db.list(limit, status, tool)


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    j = db.get(job_id)
    if j is None:
        raise HTTPException(404, "unknown job")
    return j


@app.post("/api/jobs")
def submit(tool: str = Body(...), params: dict = Body(default={}), source: str = Body(default="dashboard")):
    try:
        return runner.submit(tool, params, source)
    except (ValueError, PathError) as e:
        raise bad_request(e)


@app.post("/api/jobs/{job_id}/wait")
async def wait(job_id: str, timeout: float = 600):
    try:
        return await runner.wait(job_id, timeout)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/jobs/{job_id}/cancel")
async def cancel(job_id: str):
    try:
        return await runner.cancel(job_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/workers/{name}/unload")
async def unload(name: str):
    if name not in WORKERS:
        raise HTTPException(404, "unknown worker")
    r = await runner.http.post(f"{WORKERS[name]['url']}/unload", timeout=60)
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text)
    return r.json()


@app.get("/api/library")
def library_search(q: str = "", status: str | None = None):
    return library.search(q.split(), status or None)


@app.post("/api/library/mark")
def library_mark(file: str = Body(...), status: str | None = Body(None), used_in: str | None = Body(None),
                 tags: list[str] | None = Body(None), note: str | None = Body(None)):
    try:
        return library.mark(file, status, used_in, tags, note)
    except ValueError as e:
        raise bad_request(e)


@app.post("/api/library/scan")
def library_scan():
    return library.scan()


@app.get("/api/file")
def file(path: str = Query(...)):
    """Serve a file under the home folder (audio previews, downloads)."""
    try:
        p = Path(to_container(path))
    except PathError as e:
        raise bad_request(e)
    if not p.is_file():
        raise HTTPException(404, "no such file")
    return FileResponse(p, media_type=mimetypes.guess_type(p.name)[0] or "application/octet-stream",
                        filename=p.name, content_disposition_type="inline")


# Dashboard: a static React build. Hash routing, so "/" is the only page.
web = Path(WEB_DIR)
if (web / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=web / "assets"), name="assets")


@app.get("/", include_in_schema=False)
def index():
    if not (web / "index.html").exists():
        raise HTTPException(503, "dashboard not built")
    return FileResponse(web / "index.html")


# Last: the MCP app answers /mcp (it is a Starlette app with that one route).
app.mount("/", mcp_app)
