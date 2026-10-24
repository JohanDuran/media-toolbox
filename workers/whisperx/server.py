"""WhisperX worker: transcribe media files with word-level timings (local GPU).

Action `transcribe` params:
  files       list of input media paths
  out_dir     where <stem>.json / .srt / .words.tsv are written
  language    e.g. "en", "es"; auto-detect if empty
  model       Whisper model (default large-v3)
  stream      audio stream index (0 = first audio track, usually the mic)
  vocab       names/terms to bias recognition toward (initial prompt)
  batch_size  default 8

Times in the outputs are seconds from the start of the SOURCE file.
The model stays loaded between jobs until the gateway asks for /unload.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import torch
import whisperx

from toolbox_worker import JobContext, create_app, cuda_info, free_cuda

DEFAULT_VOCAB = os.environ.get("TRANSCRIBE_VOCAB") or "Claude, Claude Desktop, Claude Code, Anthropic, MCP, skills, DaVinci Resolve, HyperFrames, OBS."
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
COMPUTE_TYPE = "float16" if DEVICE == "cuda" else "int8"

_model = {"key": None, "asr": None}
_align: dict = {}


def get_model(name: str, language: str | None, vocab: str):
    key = (name, language, vocab)
    if _model["key"] != key:
        _model["asr"] = None
        free_cuda()
        _model["asr"] = whisperx.load_model(name, DEVICE, compute_type=COMPUTE_TYPE, language=language,
                                            asr_options={"initial_prompt": vocab or None})
        _model["key"] = key
    return _model["asr"]


def unload() -> None:
    _model.update(key=None, asr=None)
    _align.clear()
    free_cuda()


def extract_audio(src: Path, stream: int, dst: Path) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(src),
                    "-map", f"0:a:{stream}", "-ac", "1", "-ar", "16000", str(dst)], check=True)


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def write_outputs(result: dict, base: Path, source: Path, language: str) -> list[str]:
    segments = [
        {
            "start": seg.get("start"),
            "end": seg.get("end"),
            "text": seg.get("text", "").strip(),
            "words": [{k: w[k] for k in ("word", "start", "end", "score") if k in w}
                      for w in seg.get("words", [])],
        }
        for seg in result["segments"]
    ]
    js, srt, tsv = base.with_suffix(".json"), base.with_suffix(".srt"), Path(f"{base}.words.tsv")
    js.write_text(json.dumps({"source": str(source), "language": language, "segments": segments},
                             ensure_ascii=False, indent=2), encoding="utf-8")
    with srt.open("w", encoding="utf-8", newline="\r\n") as f:  # Windows line endings, as before
        for i, seg in enumerate(segments, 1):
            f.write(f"{i}\n{srt_time(seg['start'])} --> {srt_time(seg['end'])}\n{seg['text']}\n\n")
    with tsv.open("w", encoding="utf-8", newline="\r\n") as f:
        f.write("start\tend\tscore\tword\n")
        for seg in segments:
            for w in seg["words"]:
                # Words WhisperX could not align (numbers, symbols) have no timing.
                if "start" in w:
                    f.write(f"{w['start']:.3f}\t{w['end']:.3f}\t{w.get('score', 0):.2f}\t{w['word']}\n")
    return [str(js), str(srt), str(tsv)]


def transcribe(p: dict, ctx: JobContext) -> dict:
    files = [Path(f) for f in p["files"]]
    out_dir = Path(p["out_dir"])
    language = p.get("language") or None
    model_name = p.get("model") or "large-v3"
    vocab = p.get("vocab", DEFAULT_VOCAB)
    stream = int(p.get("stream", 0))
    out_dir.mkdir(parents=True, exist_ok=True)

    ctx.log(f"device={DEVICE} compute_type={COMPUTE_TYPE} model={model_name}")
    if _model["key"] != (model_name, language, vocab):
        ctx.log("loading model (first load can take a minute)")
    model = get_model(model_name, language, vocab)

    results = []
    for i, src in enumerate(files):
        ctx.check_cancel()
        ctx.log(f"== {src.name}")
        ctx.set_progress(i / len(files))
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "mic.wav"
            extract_audio(src, stream, wav)
            audio = whisperx.load_audio(str(wav))
        result = model.transcribe(audio, batch_size=int(p.get("batch_size", 8)), language=language)
        lang = result["language"]
        ctx.check_cancel()
        if lang not in _align:
            ctx.log(f"loading alignment model for {lang}")
            _align[lang] = whisperx.load_align_model(language_code=lang, device=DEVICE)
        align_model, meta = _align[lang]
        aligned = whisperx.align(result["segments"], align_model, meta, audio, DEVICE,
                                 return_char_alignments=False)
        outputs = write_outputs(aligned, out_dir / src.stem, src, lang)
        ctx.log(f"   language={lang} segments={len(aligned['segments'])} -> {out_dir / src.stem}.*")
        results.append({"source": str(src), "language": lang,
                        "segments": len(aligned["segments"]), "outputs": outputs})
    ctx.set_progress(1)
    return {"files": results}


app = create_app(
    "whisperx", whisperx.__version__ if hasattr(whisperx, "__version__") else "3.8.6",
    {"transcribe": transcribe},
    info=lambda: {**cuda_info(), "loaded_model": _model["key"][0] if _model["key"] else None},
    loaded=lambda: _model["asr"] is not None,
    unload=unload,
)
