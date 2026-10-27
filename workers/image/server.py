"""Image worker: FLUX.1 [dev] image generation through ComfyUI (the `comfyui` service), under the toolbox's GPU lock.

This worker has no GPU itself. It fills a workflow, queues it on ComfyUI's HTTP API, waits, and copies the PNGs to
out_dir. For the gateway's GPU lock it reports:
  model_loaded   true while ComfyUI holds models in VRAM (so the gateway frees it before another GPU job)
  external_busy  true while ComfyUI runs or queues work started from its own web UI (other GPU jobs then wait)
and /unload asks ComfyUI to free its models.

Action `generate` params:
  prompt, width, height (multiples of 16), count (1-8, one seed each), seed (first seed; +1 per image),
  steps, guidance, turbo (Turbo LoRA: 8 steps), crop_to [w, h] (resize-and-crop each result, e.g. 1280x720),
  out_dir, name (file name prefix)
Writes <name>_s<seed>.png plus <name>_s<seed>.json (everything needed to re-create it).
"""

import copy
import io
import json
import os
import random
import time
import uuid
from pathlib import Path

import httpx
from PIL import Image

from toolbox_worker import JobContext, create_app

os.umask(0)  # outputs readable and writable by the other workers (render runs as "node")

COMFY = os.environ.get("COMFYUI_URL", "http://comfyui:8188").rstrip("/")
WORKFLOW = json.loads((Path(__file__).parent / "flux_workflow.json").read_text())
TURBO_LORA = "flux1-turbo-alpha.safetensors"
PROMPT, NEGATIVE, GUIDANCE, LATENT, SAMPLER, SAVE = "4", "10", "5", "6", "7", "9"
JOB_TIMEOUT = 900

http = httpx.Client(base_url=COMFY, timeout=httpx.Timeout(10.0, read=60.0))
state = {"loaded": False}


def _stats() -> dict:
    return http.get("/system_stats", timeout=3).json()


def loaded() -> bool:
    """ComfyUI keeps models cached in VRAM between prompts (also ones run from its own UI)."""
    try:
        dev = _stats()["devices"][0]
        return dev.get("torch_vram_total", 0) > 512 * 2**20 or state["loaded"]
    except Exception:
        return state["loaded"]


def external_busy() -> bool:
    q = http.get("/queue", timeout=3).json()
    return bool(q.get("queue_running") or q.get("queue_pending"))


def unload() -> None:
    http.post("/free", json={"unload_models": True, "free_memory": True}, timeout=30)
    state["loaded"] = False
    # /free keeps the models in system RAM (13+ GB). Restart ComfyUI so that comes back too. (This worker itself holds
    # almost nothing, so it doesn't use TOOLBOX_RECYCLE_ON_UNLOAD.)
    if os.environ.get("COMFYUI_RECYCLE_ON_UNLOAD", "1") == "1":
        try:
            http.post("/toolbox/exit", timeout=5)
        except httpx.HTTPError:
            pass


def wait_for_comfy(ctx: JobContext, seconds: int = 180) -> None:
    """ComfyUI may be restarting (after an unload): wait for it instead of failing the job."""
    deadline = time.monotonic() + seconds
    told = False
    while True:
        try:
            _stats()
            return
        except httpx.HTTPError:
            if time.monotonic() > deadline:
                raise RuntimeError("ComfyUI isn't reachable. Start it with: docker compose up -d comfyui "
                                   "(needs the flux profile; the first start downloads the models)")
            if not told:
                ctx.log("waiting for ComfyUI to start")
                told = True
            time.sleep(2)


def info() -> dict:
    try:
        s = _stats()
        dev = s["devices"][0]
        return {"device": dev.get("name"), "comfyui": s.get("system", {}).get("comfyui_version"),
                "vram_free_mb": round(dev.get("vram_free", 0) / 2**20),
                "vram_total_mb": round(dev.get("vram_total", 0) / 2**20)}
    except Exception as e:
        return {"comfyui": f"unreachable ({e})"}


def build_workflow(p: dict, seed: int) -> dict:
    wf = copy.deepcopy(WORKFLOW)
    wf[PROMPT]["inputs"]["text"] = p["prompt"]
    wf[GUIDANCE]["inputs"]["guidance"] = float(p.get("guidance") or 3.5)
    wf[LATENT]["inputs"].update(width=int(p["width"]), height=int(p["height"]))
    wf[SAMPLER]["inputs"].update(seed=seed, steps=int(p["steps"]))
    wf[SAVE]["inputs"]["filename_prefix"] = "toolbox"
    if p.get("turbo"):
        wf["11"] = {"class_type": "LoraLoaderModelOnly",
                    "inputs": {"model": ["1", 0], "lora_name": TURBO_LORA, "strength_model": 1.0}}
        wf[SAMPLER]["inputs"]["model"] = ["11", 0]
    return wf


def _comfy_error(entry: dict) -> str:
    for kind, data in entry.get("status", {}).get("messages", []):
        if kind == "execution_error":
            return f"{data.get('exception_type')}: {data.get('exception_message', '').strip()}"
    return "ComfyUI reported an error"


def run_one(wf: dict, ctx: JobContext, base: float, share: float) -> bytes:
    r = http.post("/prompt", json={"prompt": wf, "client_id": str(uuid.uuid4())})
    if r.status_code != 200:
        raise RuntimeError(f"ComfyUI rejected the workflow: {r.text[:800]}")
    prompt_id = r.json()["prompt_id"]
    start = time.monotonic()
    while True:
        if ctx.cancel_requested:
            http.post("/queue", json={"delete": [prompt_id]})
            http.post("/interrupt")
            ctx.check_cancel()
        hist = http.get(f"/history/{prompt_id}").json()
        if prompt_id in hist:
            entry = hist[prompt_id]
            if entry.get("status", {}).get("status_str") == "error":
                raise RuntimeError(_comfy_error(entry))
            if entry.get("status", {}).get("completed", True):
                img = entry["outputs"][SAVE]["images"][0]
                return http.get("/view", params={"filename": img["filename"], "subfolder": img.get("subfolder", ""),
                                                 "type": img.get("type", "output")}).content
        if time.monotonic() - start > JOB_TIMEOUT:
            raise TimeoutError(f"no image after {JOB_TIMEOUT} s")
        ctx.set_progress(base + share * min(0.95, (time.monotonic() - start) / 60))
        time.sleep(0.5)


def _crop(png: bytes, size: list[int]) -> bytes:
    w, h = int(size[0]), int(size[1])
    img = Image.open(io.BytesIO(png)).convert("RGB")
    scale = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    buf = io.BytesIO()
    img.crop((left, top, left + w, top + h)).save(buf, format="PNG")
    return buf.getvalue()


def generate(p: dict, ctx: JobContext) -> dict:
    wait_for_comfy(ctx)
    count =max(1, min(8, int(p.get("count") or 1)))
    first = int(p["seed"]) if p.get("seed") not in (None, "") else random.randint(0, 2**31 - 1)
    out = Path(p["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    name = p.get("name") or "image"
    ctx.log(f"{count} x {p['width']}x{p['height']}, {p['steps']} steps, turbo {'on' if p.get('turbo') else 'off'}, "
            f"guidance {p.get('guidance') or 3.5}, seeds from {first}")
    files = []
    for i in range(count):
        seed = first + i
        t0 = time.monotonic()
        png = run_one(build_workflow(p, seed), ctx, i / count, 1 / count)
        state["loaded"] = True
        if p.get("crop_to"):
            png = _crop(png, p["crop_to"])
        dst = out / f"{name}_s{seed}.png"
        dst.write_bytes(png)
        secs = round(time.monotonic() - t0, 1)
        meta = {"prompt": p["prompt"], "seed": seed, "width": p["width"], "height": p["height"],
                "steps": p["steps"], "guidance": p.get("guidance") or 3.5, "turbo": bool(p.get("turbo")),
                "crop_to": p.get("crop_to"), "model": "flux1-dev-Q4_K_S.gguf", "seconds": secs}
        dst.with_suffix(".json").write_text(json.dumps(meta, indent=2))
        files.append({"path": str(dst), "seed": seed, "seconds": secs})
        ctx.log(f"{dst.name} in {secs} s")
        ctx.set_progress((i + 1) / count)
    return {"out_dir": str(out), "files": files}


app = create_app("image", "flux1-dev-Q4_K_S via ComfyUI", {"generate": generate}, info=info, loaded=loaded,
                 unload=unload, external_busy=external_busy)
