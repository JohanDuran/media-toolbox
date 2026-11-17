"""Every job the toolbox can run: which worker runs it, its parameters, and how
Windows-path params become container params (with defaults and write protection).

The dashboard builds its forms from these specs (GET /api/tools), and the MCP
tools and REST API both submit through them, so all doors behave the same.
"""

from __future__ import annotations

import os
import re
import shlex
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from . import library
from .config import OUTPUTS_DIR, SFX_LIBRARY, VOICES_DIR
from .paths import PathError, check_writable, looks_like_path, protected_dir, to_container
from .timeline import timeline_transcript

# Names and terms transcription is biased toward; set TRANSCRIBE_VOCAB in .env for your own topics.
DEFAULT_VOCAB = os.environ.get("TRANSCRIBE_VOCAB") or "Claude, Claude Desktop, Claude Code, Anthropic, MCP, skills, DaVinci Resolve, HyperFrames, OBS."


@dataclass
class Param:
    name: str
    kind: str  # text | longtext | number | integer | boolean | select | strings | path_in | paths_in | dir_in | path_out | dir_out
    label: str
    required: bool = False
    default: Any = None
    help: str = ""
    options: list | None = None


@dataclass
class Tool:
    name: str
    title: str
    worker: str  # compose service, or "gateway" for jobs the gateway runs itself
    action: str
    gpu: bool
    description: str
    params: list[Param]
    prepare: Callable[[dict], dict] | None = None  # extra conversion after the generic one
    finish: Callable[[dict, dict], dict] | None = None  # (container result, cparams) -> result
    local: Callable | None = field(default=None, repr=False)

    def public(self) -> dict:
        d = asdict(self)
        for k in ("prepare", "finish", "local"):
            d.pop(k, None)
        return d


def _slug(text: str, limit: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit].rstrip("-") or "audio"


def split_args(s: str) -> list[str]:
    """Split a typed command line, keeping Windows backslashes; quotes group words with spaces."""
    return [a[1:-1] if len(a) > 1 and a[0] == a[-1] and a[0] in "\"'" else a
            for a in shlex.split(s, posix=False)]


def _out(sub: str) -> str:
    return to_container(f"{OUTPUTS_DIR}/{sub}")


# --- tool-specific preparation ----------------------------------------------------------

def _prep_sfx(p: dict) -> dict:
    if p.get("out_dir"):
        p["sidecar"], p["catalog"] = True, False
    else:  # default: the shared, cataloged library
        p["out_dir"] = check_writable(to_container(SFX_LIBRARY))
        p["sidecar"], p["catalog"] = False, True
    return p


def _finish_sfx(result: dict, cparams: dict) -> dict:
    if cparams.get("catalog"):
        library.add_entries(result["entries"])
        result["cataloged_in"] = str(library.index_path())
    return result


def _prep_speak(p: dict) -> dict:
    if not p.get("out_path"):
        p["out_path"] = f"{_out('speech')}/{_slug(p['text'], 30)}_{time.strftime('%Y%m%d-%H%M%S')}.wav"
    return p


def voice_profiles() -> list[str]:
    """Names of the voice profiles in VOICES_DIR (<name>.wav)."""
    from pathlib import Path

    d = Path(to_container(VOICES_DIR))
    return sorted(f.stem for f in d.glob("*.wav")) if d.is_dir() else []


def _prep_speak_clone(p: dict) -> dict:
    from pathlib import Path

    if not p.get("ref_path"):
        voice = (p.get("voice") or "").strip()
        if not voice:
            raise ValueError(f"speak_clone: give a voice profile ({', '.join(voice_profiles()) or 'none yet'}) "
                             f"or a ref_path; profiles live in {VOICES_DIR}")
        p["ref_path"] = to_container(f"{VOICES_DIR}/{voice}.wav")
        if not Path(p["ref_path"]).is_file():
            raise ValueError(f"speak_clone: no voice profile {voice!r} in {VOICES_DIR} "
                             f"(have: {', '.join(voice_profiles()) or 'none'})")
    elif not Path(p["ref_path"]).is_file():
        raise ValueError(f"speak_clone: reference {p['ref_path']} not found")
    p.pop("voice", None)
    if not p.get("out_path"):
        p["out_path"] = f"{_out('speech')}/{_slug(p['text'], 30)}_clone_{time.strftime('%Y%m%d-%H%M%S')}.wav"
    return p


def _prep_ffmpeg(p: dict) -> dict:
    """Convert every path-looking argument; only `-i` inputs may point into protected folders."""
    args, out = [str(a) for a in p["args"]], []
    for i, a in enumerate(args):
        if looks_like_path(a):
            c = to_container(a)
            if protected_dir(c) and (i == 0 or args[i - 1] != "-i"):
                check_writable(c)
            out.append(c)
        else:
            out.append(a)
    p["args"] = out
    return p


def _spec_path(p: dict) -> str:
    """Validate a Shorts project up front (clear errors before the job is queued)."""
    import shorts_spec
    from pathlib import Path

    spec = f"{p['project_dir'].rstrip('/')}/short.json"
    if not Path(spec).exists():
        raise ValueError(f"no short.json in {p['project_dir']} (see the Shorts spec in the toolbox README)")
    check_writable(p["project_dir"])
    shorts_spec.Spec(Path(spec)).shots()  # raises SpecError with a readable message
    return spec


def _prep_short_build(p: dict) -> dict:
    p["spec"] = _spec_path(p)
    bad = set(p.get("parts") or []) - {"shots", "overlay", "audio"}
    if bad:
        raise ValueError(f"unknown part(s) {sorted(bad)}; use shots, overlay, audio")
    return p


def _prep_short_preview(p: dict) -> dict:
    p["spec"] = _spec_path(p)
    return p


def _prep_short_frames(p: dict) -> dict:
    p["times"] = [float(t) for t in p["times"]]
    if p.get("region"):
        p["region"] = [int(float(v)) for v in p["region"]]
        if len(p["region"]) != 4:
            raise ValueError("region must be 4 numbers: x y w h")
    if not p.get("out"):
        stem = p["src"].rstrip("/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
        p["out"] = f"{_out('frames')}/{_slug(stem, 40)}_{time.strftime('%Y%m%d-%H%M%S')}.png"
    return p


# FLUX sizes: (draft, final) per preset, multiples of 16. Drafts are ~0.6 MP for speed, finals ~1 MP.
IMAGE_SIZES = {
    "16:9": ((1024, 576), (1344, 768)),
    "9:16": ((576, 1024), (768, 1344)),
    "1:1": ((768, 768), (1024, 1024)),
    "4:3": ((896, 672), (1152, 864)),
    "3:4": ((672, 896), (864, 1152)),
    "thumbnail": ((1024, 576), (1344, 768)),  # final is resized and cropped to 1280x720
}


def _prep_image(p: dict) -> dict:
    size = str(p.get("size") or "16:9").lower()
    final = (p.get("quality") or "draft") == "final"
    if size in IMAGE_SIZES:
        w, h = IMAGE_SIZES[size][1 if final else 0]
        if size == "thumbnail" and final:
            p["crop_to"] = [1280, 720]
    elif re.fullmatch(r"\d{3,4}x\d{3,4}", size):
        w, h = (int(v) for v in size.split("x"))
        if not (256 <= w <= 2048 and 256 <= h <= 2048):
            raise ValueError("width and height must be 256 to 2048")
    else:
        raise ValueError(f"size must be one of {sorted(IMAGE_SIZES)} or WxH")
    p["width"], p["height"] = w - w % 16, h - h % 16
    turbo = p.get("turbo", True)
    p["turbo"] = turbo
    if not p.get("steps"):
        p["steps"] = 8 if turbo else (12 if not final else 20)
    if not p.get("out_dir"):
        p["out_dir"] = check_writable(_out("images"))
    p["name"] = _slug(p.get("name") or p["prompt"], 40)
    p.pop("size", None)
    p.pop("quality", None)
    return p


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool(
        "transcribe", "Transcribe", "whisperx", "transcribe", True,
        "Transcribe media files with WhisperX on the GPU: word-level timings, written as "
        "<name>.json (segments + words), .srt and .words.tsv. Times are seconds in the source file.",
        [
            Param("files", "paths_in", "Media files", True, help="One or more video/audio files"),
            Param("out_dir", "dir_out", "Output folder", default=f"{OUTPUTS_DIR}/transcripts",
                  help="e.g. <ResolveProject>\\transcripts"),
            Param("language", "text", "Language", help="en, es, ... (blank = detect)"),
            Param("model", "select", "Model", default="large-v3",
                  options=["large-v3", "large-v3-turbo", "medium", "small", "tiny"]),
            Param("stream", "integer", "Audio stream", default=0, help="0 = first audio track (usually the mic)"),
            Param("vocab", "longtext", "Vocabulary hint", default=DEFAULT_VOCAB,
                  help="Names and terms to bias recognition toward"),
            Param("batch_size", "integer", "Batch size", default=8),
        ],
    ),
    Tool(
        "timeline_transcript", "Timeline transcript", "gateway", "timeline_transcript", False,
        "Map per-file transcripts onto a Resolve timeline: reads <project>/timeline/<timeline>.audio1.json "
        "and <project>/transcripts/<stem>.json, writes <timeline>.timeline.json/.srt/.txt in timeline time.",
        [
            Param("project_dir", "dir_in", "Project folder", True, help="The folder named after the Resolve project"),
            Param("timeline", "text", "Timeline", default="Timeline 1"),
            Param("gap", "number", "Line break gap (s)", default=0.8),
        ],
        local=timeline_transcript,
    ),
    Tool(
        "generate_sfx", "Generate sound", "stable-audio", "generate", True,
        "Generate sound effects, ambience or short music beds (max 47 s) with Stable Audio Open 1.0. "
        "By default the WAVs go into the shared sound library and get cataloged; search it first.",
        [
            Param("prompt", "longtext", "Prompt", True, help='Plain description, e.g. "whoosh transition, fast, airy"'),
            Param("seconds", "number", "Seconds", default=10),
            Param("variations", "integer", "Variations", default=3),
            Param("seed", "integer", "Seed", help="Blank = random; reuse one to tweak a result"),
            Param("steps", "integer", "Steps", default=100),
            Param("guidance", "number", "Guidance", default=7.0),
            Param("negative", "text", "Negative prompt", default="low quality, distortion, noise"),
            Param("out_dir", "dir_out", "Output folder", help="Blank = shared sound library (cataloged)"),
        ],
        prepare=_prep_sfx, finish=_finish_sfx,
    ),
    Tool(
        "speak", "Text to speech", "tts", "speak", True,
        "Turn text into a voiceover WAV with Kokoro-82M (free, local). The voice's first letter picks the "
        "language: a/b English (US/UK), e Spanish, f French, i Italian, p Portuguese, h Hindi.",
        [
            Param("text", "longtext", "Text", True, help="Blank lines split paragraphs"),
            Param("voice", "select", "Voice", default="af_heart", options=[
                "af_heart", "af_alloy", "af_aoede", "af_bella", "af_jessica", "af_kore", "af_nicole",
                "af_nova", "af_river", "af_sarah", "af_sky", "am_adam", "am_echo", "am_eric", "am_fenrir",
                "am_liam", "am_michael", "am_onyx", "am_puck", "am_santa", "bf_alice", "bf_emma",
                "bf_isabella", "bf_lily", "bm_daniel", "bm_fable", "bm_george", "bm_lewis", "ef_dora",
                "em_alex", "em_santa", "ff_siwis", "if_sara", "im_nicola", "pf_dora", "pm_alex", "pm_santa",
                "hf_alpha", "hf_beta", "hm_omega", "hm_psi"]),
            Param("speed", "number", "Speed", default=1.0),
            Param("pause", "number", "Pause between paragraphs (s)", default=0.35),
            Param("out_path", "path_out", "Output .wav", help=f"Blank = {OUTPUTS_DIR}\\speech"),
        ],
        prepare=_prep_speak,
    ),
    Tool(
        "speak_clone", "Speak in a cloned voice", "voice-clone", "speak_clone", True,
        "Turn text into a voiceover WAV in a cloned voice with Chatterbox Turbo (free, local, English). Pick a "
        f"voice profile from {VOICES_DIR} or give a reference WAV (10-20 s of clean speech, > 5 s required). "
        "Numbers are spelled out first so they're read correctly. Outputs carry Resemble's inaudible AI watermark.",
        [
            Param("text", "longtext", "Text", True, help="Blank lines split paragraphs"),
            Param("voice", "text", "Voice profile", help=f"Name of a .wav in {VOICES_DIR}, e.g. jdkoder"),
            Param("ref_path", "path_in", "Reference .wav", help="Instead of a profile: any clean speech clip"),
            Param("pause", "number", "Pause between paragraphs (s)", default=0.35),
            Param("spell_numbers", "boolean", "Spell numbers out", default=True,
                  help="$43,300 -> forty-three thousand, three hundred dollars"),
            Param("temperature", "number", "Temperature", default=0.8),
            Param("seed", "integer", "Seed", help="Blank = random; reuse one for a repeatable take"),
            Param("out_path", "path_out", "Output .wav", help=f"Blank = {OUTPUTS_DIR}\\speech"),
        ],
        prepare=_prep_speak_clone,
    ),
    Tool(
        "ffmpeg", "ffmpeg", "render", "ffmpeg", False,
        "Run ffmpeg with the given arguments (Windows paths are converted). Inputs may come from a protected "
        "folder, outputs may not. Paths inside filter strings are not converted.",
        [Param("args", "strings", "Arguments", True, help='e.g. -i C:\\...\\in.mp4 -af volumedetect -f null -')],
        prepare=_prep_ffmpeg,
    ),
    Tool(
        "ffprobe", "ffprobe", "render", "ffprobe", False,
        "Read a media file's format and streams as JSON.",
        [Param("path", "path_in", "Media file", True)],
    ),
    Tool(
        "short_build", "Build Short", "render", "short_build", False,
        "Deprecated: MangoAI renders Shorts now (open the folder in MangoAI and export); kept for old projects. "
        "Build a vertical Short (1080x1920) from the project's short.json: one MP4 per shot (V1), the graphics "
        "and captions overlay (V2, transparent), the leveled narration (A1) and the music/SFX bed (A2). Returns "
        "where each file goes on the Resolve timeline.",
        [
            Param("project_dir", "dir_in", "Project folder", True, help="Folder with short.json"),
            Param("parts", "strings", "Parts", help="shots overlay audio (blank = all)"),
            Param("only", "strings", "Only these shots", help="Shot names, e.g. s04_folder (blank = all)"),
        ],
        prepare=_prep_short_build,
    ),
    Tool(
        "short_preview", "Preview Short", "render", "short_preview", False,
        "Deprecated: MangoAI previews Shorts live. "
        "Composite the built shots, overlay and audio into edit/preview.mp4, plus a contact sheet with one frame "
        "per shot (edit/preview_sheet.jpg). Build first.",
        [Param("project_dir", "dir_in", "Project folder", True, help="Folder with short.json")],
        prepare=_prep_short_preview,
    ),
    Tool(
        "short_frames", "Frame grid", "render", "short_frames", False,
        "Grab frames from a recording at given seconds into one labelled grid image, to find crop regions "
        "and the right moments for a Short.",
        [
            Param("src", "path_in", "Recording", True),
            Param("times", "strings", "Times (s)", True, help="e.g. 10 12.5 15"),
            Param("region", "strings", "Crop region", help="x y w h on the recording (blank = whole frame)"),
            Param("cols", "integer", "Columns", default=4),
            Param("out", "path_out", "Output .png", help=f"Blank = {OUTPUTS_DIR}\\frames"),
        ],
        prepare=_prep_short_frames,
    ),
    Tool(
        "image_generate", "Generate image", "image", "generate", True,
        "Generate images with FLUX.1 [dev] (GGUF Q4) in the toolbox's ComfyUI, local GPU. Writes <name>_s<seed>.png "
        "plus a .json with everything needed to re-create it. Draft = smaller and fast; re-run a draft's seed as "
        "final for full size. Images are yours to use; the model weights are non-commercial.",
        [
            Param("prompt", "longtext", "Prompt", True,
                  help='Plain sentences; put text that should appear in quotes, e.g. a sign that says "OPEN"'),
            Param("size", "text", "Size", default="16:9",
                  help="16:9, 9:16, 1:1, 4:3, 3:4, thumbnail (final = 1280x720) or WxH"),
            Param("quality", "select", "Quality", default="draft", options=["draft", "final"]),
            Param("count", "integer", "Images", default=1, help="1 to 8, one seed each"),
            Param("seed", "integer", "Seed", help="Blank = random; images get seed, seed+1, ..."),
            Param("turbo", "boolean", "Turbo LoRA (8 steps)", default=True),
            Param("steps", "integer", "Steps", help="Blank = 8 with turbo, else 12 draft / 20 final"),
            Param("guidance", "number", "Guidance", default=3.5, help="~2.5 more natural, 3.5 default, 4-5 stricter"),
            Param("name", "text", "File name", help="Blank = from the prompt"),
            Param("out_dir", "dir_out", "Output folder", help=f"Blank = {OUTPUTS_DIR}\\images"),
        ],
        prepare=_prep_image,
    ),
]}


def prepare(tool: Tool, params: dict) -> dict:
    """Validate params, fill defaults, convert paths. Raises ValueError/PathError on bad input."""
    known = {p.name for p in tool.params}
    unknown = set(params) - known
    if unknown:
        raise ValueError(f"{tool.name}: unknown parameter(s) {sorted(unknown)}; expected {sorted(known)}")
    out: dict[str, Any] = {}
    for p in tool.params:
        v = params.get(p.name)
        if v in (None, "", []):
            v = p.default
        if v in (None, "", []):
            if p.required:
                raise ValueError(f"{tool.name}: '{p.name}' is required")
            continue
        try:
            if p.kind == "integer":
                v = int(v)
            elif p.kind == "number":
                v = float(v)
            elif p.kind == "boolean":
                v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes")
            elif p.kind == "strings":
                v = [str(x) for x in v] if isinstance(v, list) else split_args(str(v))
            elif p.kind == "paths_in":
                v = [to_container(x) for x in (v if isinstance(v, list) else [v])]
            elif p.kind in ("path_in", "dir_in"):
                v = to_container(v)
            elif p.kind in ("path_out", "dir_out"):
                v = check_writable(to_container(v))
        except (TypeError, ValueError) as e:
            if isinstance(e, PathError):
                raise
            raise ValueError(f"{tool.name}: '{p.name}' has a bad value {v!r} ({e})")
        out[p.name] = v
    if tool.prepare:
        out = tool.prepare(out)
    return out
