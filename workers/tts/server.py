"""TTS worker: text to speech with Kokoro-82M (Apache 2.0, local GPU).

Action `speak` params:
  text      what to say; blank lines split it into paragraphs
  voice     e.g. af_heart (US English), bm_george (UK English), ef_dora (Spanish);
            the first letter picks the language
  speed     1.0 = normal
  out_path  .wav file to write (24 kHz mono, 16-bit)
  pause     seconds of silence between paragraphs (default 0.35)
"""

from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from kokoro import KPipeline

from toolbox_worker import JobContext, create_app, cuda_info, free_cuda

SAMPLE_RATE = 24000
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# Languages whose extra dependencies are installed in this image.
LANGUAGES = {"a": "English (US)", "b": "English (UK)", "e": "Spanish", "f": "French",
             "i": "Italian", "p": "Portuguese (BR)", "h": "Hindi"}
VOICES = {
    "a": ["af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "am_adam", "am_michael", "am_puck"],
    "b": ["bf_emma", "bf_isabella", "bm_george", "bm_fable", "bm_lewis"],
    "e": ["ef_dora", "em_alex", "em_santa"],
    "f": ["ff_siwis"],
    "i": ["if_sara", "im_nicola"],
    "p": ["pf_dora", "pm_alex"],
    "h": ["hf_alpha", "hm_omega"],
}

_pipes: dict[str, KPipeline] = {}


def pipeline(lang: str, ctx: JobContext) -> KPipeline:
    if lang not in _pipes:
        ctx.log(f"loading Kokoro for {LANGUAGES.get(lang, lang)}")
        # Every language pipeline shares one model; load it once and hand it over.
        shared = next(iter(_pipes.values())).model if _pipes else True
        _pipes[lang] = KPipeline(lang_code=lang, repo_id="hexgrad/Kokoro-82M", model=shared, device=DEVICE)
    return _pipes[lang]


def unload() -> None:
    _pipes.clear()
    free_cuda()


def speak(p: dict, ctx: JobContext) -> dict:
    text = p["text"].strip()
    if not text:
        raise ValueError("text is empty")
    voice = p.get("voice") or "af_heart"
    lang = voice[0]
    if lang not in LANGUAGES:
        raise ValueError(f"voice {voice!r}: language {lang!r} isn't installed; have {sorted(LANGUAGES)}")
    speed = float(p.get("speed", 1.0))
    out = Path(p["out_path"])
    out.parent.mkdir(parents=True, exist_ok=True)
    gap = np.zeros(int(float(p.get("pause", 0.35)) * SAMPLE_RATE), dtype=np.float32)

    pl = pipeline(lang, ctx)
    ctx.log(f"voice={voice} speed={speed} device={DEVICE}")
    chunks = []
    paragraphs = [t for t in text.split("\n\n") if t.strip()]
    for i, para in enumerate(paragraphs):
        ctx.check_cancel()
        for res in pl(para, voice=voice, speed=speed, split_pattern=r"\n+"):
            audio = res.audio
            if audio is not None:
                chunks.append(audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio))
        chunks.append(gap)
        ctx.set_progress((i + 1) / len(paragraphs))
    data = np.concatenate(chunks[:-1]) if len(chunks) > 1 else chunks[0]
    sf.write(out, data, SAMPLE_RATE, subtype="PCM_16")
    seconds = round(len(data) / SAMPLE_RATE, 2)
    ctx.log(f"wrote {out.name} ({seconds}s)")
    return {"path": str(out), "seconds": seconds, "voice": voice, "sample_rate": SAMPLE_RATE}


app = create_app(
    "tts", "kokoro-82m",
    {"speak": speak},
    info=lambda: {**cuda_info(), "languages": LANGUAGES, "voices": VOICES},
    loaded=lambda: bool(_pipes),
    unload=unload,
)
