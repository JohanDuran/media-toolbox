"""Stable Audio worker: generate SFX / music beds with Stable Audio Open 1.0 (local GPU).

Action `generate` params:
  prompt, seconds (<= 47), variations, seed, steps, guidance, negative
  out_dir   folder to write <slug>_s<seed>_<n>.wav into
  sidecar   true = also write <slug>_s<seed>.json with the prompt and seed
            (used for one-off sounds outside the shared library; library sounds
            are cataloged by the gateway instead)

Returns one catalog entry per WAV (file, prompt, seed, peak_db, ...).
"""

import json
import math
import random
import re
from pathlib import Path

import soundfile as sf
import torch
from diffusers import StableAudioPipeline

from toolbox_worker import JobContext, create_app, cuda_info, free_cuda

MODEL = "stabilityai/stable-audio-open-1.0"
MAX_SECONDS = 47.0
_pipe = {"p": None}


def slugify(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit].rstrip("-") or "audio"


def peak_db(data) -> float:
    peak = float(abs(data).max()) if data.size else 0.0
    return round(20 * math.log10(peak), 1) if peak > 0 else -120.0


def pipe(ctx: JobContext):
    if _pipe["p"] is None:
        ctx.log(f"loading {MODEL}")
        _pipe["p"] = StableAudioPipeline.from_pretrained(MODEL, torch_dtype=torch.float16).to("cuda")
    return _pipe["p"]


def unload() -> None:
    _pipe["p"] = None
    free_cuda()


def generate(p: dict, ctx: JobContext) -> dict:
    seconds = float(p.get("seconds", 10))
    if not 0 < seconds <= MAX_SECONDS:
        raise ValueError(f"seconds must be between 0 and {MAX_SECONDS}")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU not available; refusing to run on CPU (far too slow)")
    prompt = p["prompt"]
    variations = int(p.get("variations", 3))
    steps = int(p.get("steps", 100))
    guidance = float(p.get("guidance", 7.0))
    negative = p.get("negative", "low quality, distortion, noise")
    seed = p.get("seed")
    seed = int(seed) if seed not in (None, "") else random.randint(0, 2**31 - 1)
    out = Path(p["out_dir"])
    out.mkdir(parents=True, exist_ok=True)

    pl = pipe(ctx)
    sample_rate = pl.vae.sampling_rate
    ctx.log(f"generating {variations} x {seconds:g}s, seed {seed}, {steps} steps")

    def on_step(step, _t, _latents):
        ctx.set_progress((step + 1) / steps)
        ctx.check_cancel()  # raising here stops the denoising loop

    audios = pl(
        prompt,
        negative_prompt=negative or None,
        num_inference_steps=steps,
        guidance_scale=guidance,
        audio_end_in_s=seconds,
        num_waveforms_per_prompt=variations,
        generator=torch.Generator("cuda").manual_seed(seed),
        callback=on_step,
    ).audios

    base = f"{slugify(prompt)}_s{seed}"
    entries = []
    for i, audio in enumerate(audios, 1):
        # (channels, samples) -> (samples, channels); trim the model's fixed-length padding
        data = audio.T.float().cpu().numpy()[: int(seconds * sample_rate)]
        path = out / f"{base}_{i}.wav"
        sf.write(path, data, sample_rate, subtype="PCM_24")
        entries.append({
            "file": path.name, "path": str(path), "prompt": prompt, "negative_prompt": negative,
            "seconds": seconds, "seed": seed, "variation": i, "steps": steps,
            "guidance": guidance, "peak_db": peak_db(data),
        })
        ctx.log(f"wrote {path.name} (peak {entries[-1]['peak_db']} dB)")

    if p.get("sidecar"):
        meta = {"model": MODEL, "prompt": prompt, "negative_prompt": negative, "seconds": seconds,
                "seed": seed, "steps": steps, "guidance": guidance, "sample_rate": sample_rate,
                "files": [e["file"] for e in entries]}
        (out / f"{base}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {"seed": seed, "entries": entries}


app = create_app(
    "stable-audio", "stable-audio-open-1.0",
    {"generate": generate},
    info=cuda_info,
    loaded=lambda: _pipe["p"] is not None,
    unload=unload,
)
