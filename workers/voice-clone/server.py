"""Voice-clone worker: text to speech in a cloned voice with Chatterbox Turbo (Resemble AI, MIT, local GPU).

Kokoro (the tts worker) stays the default voice; this one is for when a video should sound like a real person
from a short reference recording. Picked over Fish S2 Pro after a blind bench on an 8 GB RTX 5060: word-perfect,
fastest (~real time), ~4 GB VRAM. Chatterbox is retired upstream (Aug 2026), so its versions are pinned.

Action `speak_clone` params:
  text           what to say; blank lines split paragraphs
  ref_path       reference .wav of the voice: 10-20 s of clean speech (only the first ~15 s are used; > 5 s required)
  out_path       .wav file to write (24 kHz mono, 16-bit)
  pause          seconds of silence between paragraphs (default 0.35)
  spell_numbers  write numbers as words first (default true; see spell.py)
  temperature    sampling temperature (default 0.8)
  seed           fixed seed for a repeatable take (optional)

Every output carries Resemble's inaudible Perth watermark (marks it as AI-generated).
"""

import re
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

from spell import spell_numbers
from toolbox_worker import JobContext, create_app, cuda_info, free_cuda

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
MAX_CHUNK = 280  # characters per generation; Turbo stops at ~40 s of speech per call
CHUNK_GAP = 0.12  # seconds between chunks of one paragraph

_state: dict = {"model": None, "ref": None}


def model(ctx: JobContext):
    if _state["model"] is None:
        from chatterbox.tts_turbo import ChatterboxTurboTTS

        ctx.log("loading Chatterbox Turbo")
        _state["model"] = ChatterboxTurboTTS.from_pretrained(device=DEVICE)
        _state["ref"] = None
    return _state["model"]


def use_reference(m, ref: Path, ctx: JobContext) -> None:
    """Encode the reference once; reuse it while the same file (same mtime) is asked for again."""
    key = (str(ref), ref.stat().st_mtime)
    if _state["ref"] != key:
        info = sf.info(str(ref))
        if info.duration <= 5.0:
            raise ValueError(f"reference {ref.name} is {info.duration:.1f} s; it needs more than 5 s of speech")
        ctx.log(f"encoding reference {ref.name} ({info.duration:.1f} s)")
        m.prepare_conditionals(str(ref))
        _state["ref"] = key


def chunks(paragraph: str) -> list[str]:
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", " ".join(paragraph.split())) if s]
    out, cur = [], ""
    for s in sentences:
        if cur and len(cur) + 1 + len(s) > MAX_CHUNK:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    return out + ([cur] if cur else [])


def unload() -> None:
    _state["model"], _state["ref"] = None, None
    free_cuda()


def speak_clone(p: dict, ctx: JobContext) -> dict:
    text = p["text"].strip()
    if not text:
        raise ValueError("text is empty")
    ref = Path(p["ref_path"])
    if not ref.is_file():
        raise ValueError(f"reference {ref} not found")
    out = Path(p["out_path"])
    out.parent.mkdir(parents=True, exist_ok=True)
    if p.get("spell_numbers", True):
        text = spell_numbers(text)
    temperature = float(p.get("temperature", 0.8))
    if p.get("seed") is not None:
        torch.manual_seed(int(p["seed"]))

    m = model(ctx)
    use_reference(m, ref, ctx)
    sr = m.sr
    pause = np.zeros(int(float(p.get("pause", 0.35)) * sr), dtype=np.float32)
    gap = np.zeros(int(CHUNK_GAP * sr), dtype=np.float32)

    paragraphs = [chunks(t) for t in text.split("\n\n") if t.strip()]
    total = sum(len(c) for c in paragraphs)
    ctx.log(f"{total} chunk(s) in {len(paragraphs)} paragraph(s), device={DEVICE}")
    audio, done = [], 0
    for para in paragraphs:
        for i, chunk in enumerate(para):
            ctx.check_cancel()
            wav = m.generate(chunk, temperature=temperature)
            audio.append(wav.squeeze(0).detach().cpu().numpy().astype(np.float32))
            audio.append(gap if i < len(para) - 1 else pause)
            done += 1
            ctx.set_progress(done / total)
    data = np.concatenate(audio[:-1])
    sf.write(out, data, sr, subtype="PCM_16")
    seconds = round(len(data) / sr, 2)
    ctx.log(f"wrote {out.name} ({seconds}s)")
    return {"path": str(out), "seconds": seconds, "sample_rate": sr, "reference": str(ref),
            "chunks": total, "text_spoken": text, "watermarked": True}


app = create_app(
    "voice-clone", "chatterbox-turbo",
    {"speak_clone": speak_clone},
    info=lambda: {**cuda_info(), "model": "ResembleAI/chatterbox-turbo"},
    loaded=lambda: _state["model"] is not None,
    unload=unload,
)
