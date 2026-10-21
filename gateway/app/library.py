"""Shared sound library: every generated (or dropped-in) sound and what you thought of it.

Lives in SFX_LIBRARY next to one catalog file, index.jsonl (one JSON object per WAV):
  file, prompt, negative_prompt, seconds, seed, variation, steps, guidance,
  created, peak_db, status (new | keep | reject), used_in [projects], tags, notes
The gateway owns it, so the catalog stays usable even while the GPU worker is down.
"""

import fnmatch
import json
import math
import threading
from datetime import datetime
from pathlib import Path

from .config import SFX_LIBRARY
from .paths import to_container, to_windows

_lock = threading.Lock()


def library_dir() -> Path:
    return Path(to_container(SFX_LIBRARY))


def index_path() -> Path:
    return library_dir() / "index.jsonl"


def load() -> list[dict]:
    idx = index_path()
    if not idx.exists():
        return []
    return [json.loads(line) for line in idx.read_text(encoding="utf-8").splitlines() if line.strip()]


def _save(entries: list[dict]) -> None:
    lib = library_dir()
    lib.mkdir(parents=True, exist_ok=True)
    tmp = index_path().with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries), encoding="utf-8")
    tmp.replace(index_path())


def _with_path(e: dict) -> dict:
    return {**e, "path": to_windows(str(library_dir() / e["file"]))}


def add_entries(new: list[dict]) -> None:
    with _lock:
        library_dir().mkdir(parents=True, exist_ok=True)
        with index_path().open("a", encoding="utf-8") as f:
            for e in new:
                e = {k: v for k, v in e.items() if k != "path"}
                e.setdefault("created", datetime.now().isoformat(timespec="seconds"))
                e.setdefault("status", "new")
                e.setdefault("used_in", [])
                e.setdefault("tags", [])
                e.setdefault("notes", "")
                f.write(json.dumps(e, ensure_ascii=False) + "\n")


def search(words: list[str] | None = None, status: str | None = None) -> list[dict]:
    words = [w.lower() for w in (words or []) if w.strip()]
    hits = []
    for e in load():
        if status and e.get("status") != status:
            continue
        hay = " ".join([e.get("file", ""), e.get("prompt", ""), e.get("notes", ""),
                        " ".join(e.get("tags", []))]).lower()
        if all(w in hay for w in words):
            hits.append(_with_path(e))
    hits.sort(key=lambda e: e.get("created", ""), reverse=True)
    return hits


def mark(pattern: str, status: str | None = None, used_in: str | None = None,
         tags: list[str] | None = None, note: str | None = None) -> list[dict]:
    if status not in (None, "", "new", "keep", "reject"):
        raise ValueError("status must be new, keep or reject")
    with _lock:
        entries = load()
        hits = [e for e in entries if fnmatch.fnmatch(e["file"], pattern) or e["file"] == pattern]
        if not hits:
            raise ValueError(f"no catalog entry matches {pattern!r}")
        for e in hits:
            if status:
                e["status"] = status
            if used_in and used_in not in e.setdefault("used_in", []):
                e["used_in"].append(used_in)
            for t in tags or []:
                if t and t not in e.setdefault("tags", []):
                    e["tags"].append(t)
            if note:
                e["notes"] = (e.get("notes", "") + " | " if e.get("notes") else "") + note
        _save(entries)
    return [_with_path(e) for e in hits]


def peak_db(data) -> float:
    peak = float(abs(data).max()) if data.size else 0.0
    return round(20 * math.log10(peak), 1) if peak > 0 else -120.0


def scan() -> dict:
    """Catalog WAVs that were dropped into the library by hand."""
    import soundfile as sf

    lib = library_dir()
    with _lock:
        entries = load()
    known = {e["file"] for e in entries}
    # sidecar .json files from older generate.py runs carry the prompt and seed
    sidecars = {}
    for js in lib.glob("*.json"):
        meta = json.loads(js.read_text(encoding="utf-8"))
        for i, name in enumerate(meta.get("files", []), 1):
            sidecars[name] = (meta, i, js)
    new = []
    for wav in sorted(lib.glob("*.wav")):
        if wav.name in known:
            continue
        data, sr = sf.read(wav)
        e = {"file": wav.name, "seconds": round(len(data) / sr, 2), "peak_db": peak_db(data),
             "created": datetime.fromtimestamp(wav.stat().st_mtime).isoformat(timespec="seconds")}
        if wav.name in sidecars:
            meta, i, _ = sidecars[wav.name]
            e.update(prompt=meta["prompt"], negative_prompt=meta.get("negative_prompt"), seed=meta["seed"],
                     variation=i, steps=meta.get("steps"), guidance=meta.get("guidance"))
        else:
            e["prompt"] = ""
            e["notes"] = "not generated here (dropped into the library)"
        new.append(e)
    add_entries(new)
    cataloged = known | {e["file"] for e in new}
    for js in {js for _, _, js in sidecars.values()}:
        if all(name in cataloged for name in json.loads(js.read_text(encoding="utf-8"))["files"]):
            js.unlink()  # folded into the catalog
    return {"cataloged": [e["file"] for e in new], "library": to_windows(str(lib))}
