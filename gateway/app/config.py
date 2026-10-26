"""Gateway settings (from docker-compose / .env)."""

import os


def _norm(p: str) -> str:
    return p.strip().strip('"').replace("\\", "/").rstrip("/")


HOST_HOME = _norm(os.environ["HOST_HOME"])  # required: set it in .env (see .env.example)
CONTAINER_HOME = "/host"
OUTPUTS_DIR = _norm(os.environ.get("OUTPUTS_DIR", f"{HOST_HOME}/media-toolbox-outputs"))
SFX_LIBRARY = _norm(os.environ.get("SFX_LIBRARY", f"{OUTPUTS_DIR}/sfx_library"))
PROTECTED_DIRS = [_norm(p) for p in os.environ.get("PROTECTED_DIRS", "").split(",") if p.strip()]
# Where the dashboard looks for Shorts projects (folders with a short.json, up to 2 levels deep).
SHORTS_ROOT = _norm(os.environ.get("SHORTS_ROOT", f"{HOST_HOME}/Documents/Projects"))
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8765")
DB_PATH = os.environ.get("DB_PATH", "/cache/gateway/jobs.sqlite")
WEB_DIR = os.environ.get("WEB_DIR", "/app/web")

# Every worker, by compose service name. gpu=True workers share one GPU lock:
# only one of them runs a job at a time, and the others are asked to free VRAM first.
WORKERS = {
    "whisperx": {"url": "http://whisperx:8000", "gpu": True, "title": "WhisperX transcription"},
    "stable-audio": {"url": "http://stable-audio:8000", "gpu": True, "title": "Stable Audio SFX"},
    "tts": {"url": "http://tts:8000", "gpu": True, "title": "Kokoro text to speech"},
    "vision": {"url": "http://vision:8000", "gpu": True, "title": "Depth + cutout"},
    "render": {"url": "http://render:8000", "gpu": False, "title": "HyperFrames + ffmpeg render"},
}
