"""Render worker (CPU): HyperFrames renders, ffmpeg and ffprobe.

Actions:
  render   project_dir, output, composition, format, quality, fps, extra_args
           -> npx-free `hyperframes render` with the version pinned in this image
  ffmpeg   args: list of ffmpeg arguments (paths already converted by the gateway)
  ffprobe  path -> format + streams as JSON
  short_build    spec (short.json), parts, only -> vertical Short shots, overlay, audio (shorts_build.py)
  short_preview  spec -> everything composited into one MP4 + a contact sheet
  short_frames   src, times, out, region -> a labelled grid of frames, for planning
"""

import json
import os
import subprocess
from pathlib import Path

import shorts_build
from toolbox_worker import JobContext, create_app


def version(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip().splitlines()[0]
    except Exception as e:
        return f"unavailable ({e})"


VERSIONS = {"hyperframes": version(["hyperframes", "--version"]),
            "ffmpeg": version(["ffmpeg", "-hide_banner", "-version"]),
            "node": version(["node", "--version"])}


def stream(cmd: list[str], ctx: JobContext, cwd: str | None = None) -> None:
    ctx.log("$ " + " ".join(cmd))
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", bufsize=1, env={**os.environ, "FORCE_COLOR": "0"})
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if line.startswith("@hf-progress "):  # HyperFrames' machine-readable progress
                try:
                    ctx.set_progress(json.loads(line.split(" ", 1)[1])["pct"] / 100)
                except (ValueError, KeyError):
                    pass
                continue
            if line:
                ctx.log(line)
            if ctx.cancel_requested:
                proc.kill()
                break
    finally:
        code = proc.wait()
    ctx.check_cancel()
    if code != 0:
        raise RuntimeError(f"{cmd[0]} exited with code {code}")


def render(p: dict, ctx: JobContext) -> dict:
    project = Path(p["project_dir"])
    if not (project / "index.html").exists() and not p.get("composition"):
        raise FileNotFoundError(f"no index.html in {project}")
    output = Path(p["output"])
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["hyperframes", "render", str(project), "--output", str(output)]
    for flag in ("composition", "format", "quality", "fps"):
        if p.get(flag) not in (None, ""):
            cmd += [f"--{flag}", str(p[flag])]
    cmd += [str(a) for a in p.get("extra_args") or []]
    stream(cmd, ctx, cwd=str(project))
    return {"output": str(output), "bytes": output.stat().st_size if output.exists() else None}


def ffmpeg(p: dict, ctx: JobContext) -> dict:
    args = [str(a) for a in p["args"]]
    stream(["ffmpeg", "-hide_banner", "-nostdin", *args], ctx)
    return {"ok": True, "log_tail": ctx.logs[-15:]}


def ffprobe(p: dict, ctx: JobContext) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                          p["path"]], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip())
    return json.loads(out.stdout)


def short_build(p: dict, ctx: JobContext) -> dict:
    parts = p.get("parts") or ["shots", "overlay", "audio"]
    return shorts_build.build(Path(p["spec"]), parts, p.get("only") or [], ctx.log)


def short_preview(p: dict, ctx: JobContext) -> dict:
    return shorts_build.preview(Path(p["spec"]), ctx.log)


def short_frames(p: dict, ctx: JobContext) -> dict:
    return shorts_build.frames(Path(p["src"]), [float(t) for t in p["times"]], Path(p["out"]),
                               p.get("region"), int(p.get("cols", 4)))


app = create_app("render", VERSIONS["hyperframes"],
                 {"render": render, "ffmpeg": ffmpeg, "ffprobe": ffprobe,
                  "short_build": short_build, "short_preview": short_preview, "short_frames": short_frames},
                 info=lambda: {"device": "cpu", **VERSIONS})
