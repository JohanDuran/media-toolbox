"""Shorts projects as the dashboard and Claude see them: find folders with a short.json and
summarize a spec (shots, times, which pieces are built). Reading only; building runs as jobs
on the render worker (tools: short_build, short_preview)."""

from __future__ import annotations

from pathlib import Path

import shorts_spec

from .config import SHORTS_ROOT
from .paths import to_container, to_windows


def _built(p: Path) -> dict:
    return {"path": to_windows(str(p)), "exists": p.exists(),
            "mtime": p.stat().st_mtime if p.exists() else None}


def find_projects() -> list[dict]:
    root = Path(to_container(SHORTS_ROOT))
    found = sorted(set(root.glob("*/short.json")) | set(root.glob("*/*/short.json")))
    out = []
    for spec_file in found:
        item = {"project_dir": to_windows(str(spec_file.parent)), "name": spec_file.parent.name}
        try:
            s = shorts_spec.Spec(spec_file).summary()
            item.update(title=s["title"], length_s=s["length_s"], shots=len(s["shots"]), error=None)
        except Exception as e:  # show broken specs too, with the reason
            item.update(title=spec_file.parent.name, error=str(e))
        item["preview"] = _built(spec_file.parent / "edit" / "preview.mp4")
        out.append(item)
    return out


def info(project_dir: str) -> dict:
    """Spec summary + which outputs exist (Windows paths), for one project folder."""
    proj = Path(to_container(project_dir))
    spec_file = proj / "short.json"
    if not spec_file.exists():
        raise ValueError(f"no short.json in {project_dir}")
    spec = shorts_spec.Spec(spec_file)
    s = spec.summary()
    shots_dir = spec.out("shots", "edit/shots")
    for shot in s["shots"]:
        shot["file"] = _built(shots_dir / f"{shot['name']}.mp4")
        shot["thumb"] = _built(shots_dir / "thumbs" / f"{shot['name']}.jpg")
    s["project_dir"] = to_windows(str(proj))
    s["outputs"] = {k: _built(spec.out(k, d)) for k, d in (
        ("overlay", "edit/overlay.mov"), ("narration", "audio/narration_final.wav"), ("bed", "audio/bed.wav"),
        ("preview", "edit/preview.mp4"))}
    s["outputs"]["sheet"] = _built(spec.out("preview", "edit/preview.mp4").with_name("preview_sheet.jpg"))
    s["graphics_list"] = [{"start": round(g["start"], 2), "end": round(g["end"], 2),
                           "text": g.get("text") or "".join(t for t, _ in g.get("runs", [])) or
                           ("[card]" if "shape" in g else "")} for g in spec.graphics()]
    return s
