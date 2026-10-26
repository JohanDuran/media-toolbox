"""Render worker (CPU): HyperFrames renders, ffmpeg and ffprobe.

Actions:
  render   project_dir, output, composition, format, quality, fps, extra_args
           -> npx-free `hyperframes render` with the version pinned in this image
  ffmpeg   args: list of ffmpeg arguments (paths already converted by the gateway)
  ffprobe  path -> format + streams as JSON
  short_build    spec (short.json), parts, only -> vertical Short shots, overlay, audio (shorts_build.py)
  short_preview  spec -> everything composited into one MP4 + a contact sheet
  short_frames   src, times, out, region -> a labelled grid of frames, for planning
  parallax       image + depth / cutout / plate (from the vision worker) -> a moving 2.5D shot rendered from
                 templates/parallax with HyperFrames
"""

import json
import os
import shutil
import subprocess
import tempfile
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


TEMPLATES = Path(__file__).parent / "templates"


def _image_size(path: str) -> tuple[int, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "json", path], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"can't read image size of {path}: {out.stderr.strip()}")
    st = json.loads(out.stdout)["streams"][0]
    return int(st["width"]), int(st["height"])


def parallax(p: dict, ctx: JobContext) -> dict:
    mode = p.get("mode") or "depth"
    need = ["image", "depth"] if mode == "depth" else ["plate", "cut"]
    for key in need:
        if not p.get(key) or not Path(p[key]).exists():
            raise FileNotFoundError(f"{mode} mode needs the {key} file ({p.get(key)})")
    if p.get("size") in (None, "", "source"):
        w, h = _image_size(p["image"])
        scale = min(1.0, 3840 / max(w, h))
        w, h = int(w * scale), int(h * scale)
    else:
        w, h = (int(v) for v in str(p["size"]).lower().split("x"))
    w, h = w - w % 2, h - h % 2
    duration = float(p.get("duration") or 5)
    output = Path(p["output"])
    output.parent.mkdir(parents=True, exist_ok=True)

    work = Path(tempfile.mkdtemp(prefix="parallax-"))
    try:
        shutil.copytree(TEMPLATES / "parallax", work, dirs_exist_ok=True)
        (work / "assets").mkdir(exist_ok=True)
        files = {}
        for key in need:
            dst = work / "assets" / f"{key}{Path(p[key]).suffix.lower()}"
            shutil.copyfile(p[key], dst)
            files[key] = f"assets/{dst.name}"
        focus = float(p["focus"]) if p.get("focus") is not None else 0.35
        params = {"mode": mode, "move": p.get("move") or "push-in", "duration": duration,
                  "strength": float(p.get("strength") or 1.0), "focus": focus,
                  "center": [float(v) for v in (p.get("center") or [0.5, 0.5])], "width": w, "height": h,
                  "files": files}
        (work / "assets" / "params.js").write_text(f"window.PARALLAX = {json.dumps(params)};\n")
        html = (work / "index.html").read_text()
        for k, v in {"__W__": str(w), "__H__": str(h), "__DURATION__": f"{duration:g}"}.items():
            html = html.replace(k, v)
        (work / "index.html").write_text(html)
        ctx.log(f"parallax {mode} {params['move']} {w}x{h} {duration:g}s strength {params['strength']:g}")
        cmd = ["hyperframes", "render", str(work), "--output", str(output)]
        for flag in ("format", "quality", "fps"):
            if p.get(flag) not in (None, ""):
                cmd += [f"--{flag}", str(p[flag])]
        stream(cmd, ctx, cwd=str(work))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"output": str(output), "size": [w, h], "duration": duration, "mode": mode, "move": params["move"],
            "bytes": output.stat().st_size if output.exists() else None}


app = create_app("render", VERSIONS["hyperframes"],
                 {"render": render, "ffmpeg": ffmpeg, "ffprobe": ffprobe,
                  "short_build": short_build, "short_preview": short_preview, "short_frames": short_frames,
                  "parallax": parallax},
                 info=lambda: {"device": "cpu", **VERSIONS})
