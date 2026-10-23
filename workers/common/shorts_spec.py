"""short.json: the data spec for a vertical Short, and the code that resolves it.

A spec lives in a project folder and describes the whole edit as data: sources (screen
recordings), regions to crop, the shots with their zoom keys, the graphics, the captions and
the audio. Every time can be anchored to a word of the narration, so a new voice take re-times
the edit by itself. Shared by the gateway (to show and validate projects) and the render worker
(to build them). Pure standard library.

Time expressions (strings), terms joined by " + " / " - " (spaces around the operator):
  1.5                 seconds
  word:so             start of the 1st "so" in the narration   (case/punctuation ignored)
  word:use#2          start of the 2nd "use"
  word:so.end         end of the 1st "so"
  chat                a named anchor (from "anchors", resolved in order)
  words_end           end of the last spoken word
  start / dur         inside a shot's zoom keys: the shot's start time / its length
e.g. "word:this#2 - 0.06", "word:folder - start + 0.3", "words_end + 0.9"

See docs/short-spec.md for the full format and an example.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

FPS = 30
_OP = re.compile(r"\s+([+-])\s+")
_WORD = re.compile(r"^word:(?P<w>[^#.]+)(?:#(?P<n>\d+))?(?P<end>\.end)?$")

COLORS = {  # ASS colours are &HAABBGGRR
    "white": "&H00FFFFFF", "yellow": "&H0000D4FF", "green": "&H0084DC3D", "red": "&H004D4DFF",
    "orange": "&H005777D9", "grey": "&H00A0A0A0", "blue": "&H00F0A050", "darkgreen": "&H00349E2B",
    "ink": "&H00241F1E", "card": "&H002A2220", "black": "&H00000000",
}


class SpecError(ValueError):
    pass


def ass_color(c: str) -> str:
    c = str(c).strip()
    if c.lower() in COLORS:
        return COLORS[c.lower()]
    if re.fullmatch(r"#?[0-9a-fA-F]{6}", c):
        h = c.lstrip("#")
        return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()
    if c.startswith("&H"):
        return c
    raise SpecError(f"unknown colour {c!r} (use a name: {', '.join(COLORS)}, or #RRGGBB)")


def frame(t: float) -> int:
    return round(t * FPS)


def load_words(tsv: Path) -> list[dict]:
    with tsv.open(encoding="utf-8") as f:
        return [{"w": r["word"], "s": float(r["start"]), "e": float(r["end"])}
                for r in csv.DictReader(f, delimiter="\t")]


def _norm(w: str) -> str:
    return re.sub(r"[^\w'-]", "", w).lower()


class Spec:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.root = self.path.parent
        try:
            self.data: dict = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SpecError(f"{self.path.name}: invalid JSON ({e})")
        d = self.data
        self.fps = int(d.get("fps", FPS))
        if self.fps != FPS:
            raise SpecError("only 30 fps is supported for now")
        words_file = self.file(d.get("words", "transcripts/narration.words.tsv"))
        if not words_file.exists():
            raise SpecError(f"word timings not found: {words_file} (transcribe the narration first)")
        self.words = load_words(words_file)
        self.anchors: dict[str, float] = {}
        for name, expr in d.get("anchors", {}).items():
            self.anchors[name] = self.time(expr)

    # --- paths and lookups ---------------------------------------------------------------
    def file(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else (self.root / p).resolve()

    def out(self, key: str, default: str) -> Path:
        return self.file(self.data.get("outputs", {}).get(key, default))

    def source(self, ref: str) -> Path:
        return self.file(self.data.get("sources", {}).get(ref, ref))

    def region(self, ref) -> list[int]:
        r = self.data.get("regions", {}).get(ref, ref) if isinstance(ref, str) else ref
        if not (isinstance(r, list) and len(r) == 4):
            raise SpecError(f"region {ref!r} must be [x, y, w, h] or a name from 'regions'")
        return [int(v) for v in r]

    def blurs(self, refs) -> list[list[int]]:
        out = []
        for ref in refs or []:
            r = self.data.get("blurs", {}).get(ref, ref) if isinstance(ref, str) else ref
            out += [self.region(x) for x in (r if isinstance(r[0], list) else [r])]
        return out

    # --- time expressions ----------------------------------------------------------------
    def word(self, ref: str) -> float:
        m = _WORD.match(ref)
        if not m:
            raise SpecError(f"bad word reference {ref!r} (use word:so, word:use#2, word:so.end)")
        target, n = _norm(m["w"]), int(m["n"] or 1)
        hits = [w for w in self.words if _norm(w["w"]) == target]
        if len(hits) < n:
            raise SpecError(f"{ref!r}: the narration says {m['w']!r} only {len(hits)} time(s)")
        return hits[n - 1]["e" if m["end"] else "s"]

    def time(self, expr, extra: dict | None = None) -> float:
        if isinstance(expr, (int, float)):
            return float(expr)
        parts = _OP.split(str(expr).strip())
        total, sign = 0.0, 1
        for i, tok in enumerate(parts):
            if i % 2:
                sign = 1 if tok == "+" else -1
                continue
            total += sign * self._term(tok, extra or {})
        return round(total, 4)

    def _term(self, tok: str, extra: dict) -> float:
        try:
            return float(tok)
        except ValueError:
            pass
        if tok.startswith("word:"):
            return self.word(tok)
        if tok in extra:
            return extra[tok]
        if tok in self.anchors:
            return self.anchors[tok]
        if tok == "words_end":
            return self.words[-1]["e"]
        raise SpecError(f"unknown time term {tok!r}")

    # --- resolved edit -------------------------------------------------------------------
    def shots(self) -> list[dict]:
        """Shots back to back: each starts where the previous one ends."""
        out, t0 = [], 0.0
        for s in self.data.get("shots", []):
            if "name" not in s or "end" not in s:
                raise SpecError("every shot needs a 'name' and an 'end'")
            t1 = self.time(s["end"])
            if frame(t1) <= frame(t0):
                raise SpecError(f"shot {s['name']}: ends at {t1:.2f}s, not after its start {t0:.2f}s")
            ctx = {"start": t0, "dur": t1 - t0}
            layers = []
            for L in s.get("layers", []):
                lay = {
                    "src": self.source(L["src"]), "still": bool(L.get("still", False)),
                    "ss": self.time(L.get("ss", 0)), "speed": float(L.get("speed", 1)),
                    "reg": self.region(L["region"]), "blur": self.blurs(L.get("blur")),
                    "card_w": int(L.get("card_w", 1080)), "cy": int(L.get("cy", 900)), "dim": bool(L.get("dim", False)),
                    "keys": [(self.time(k[0], ctx), float(k[1]), float(k[2]), float(k[3])) for k in L.get("keys", [])],
                }
                if L.get("speed_to"):  # fit this source span exactly into the shot: [from, to] seconds
                    a, b = (self.time(x) for x in L["speed_to"])
                    lay["ss"], lay["speed"] = a, (b - a) / (t1 - t0)
                if not lay["src"].exists():
                    raise SpecError(f"shot {s['name']}: source not found: {lay['src']}")
                layers.append(lay)
            out.append({"name": s["name"], "t0": t0, "t1": t1, "layers": layers, "note": s.get("note", "")})
            t0 = t1
        if not out:
            raise SpecError("the spec has no shots")
        return out

    def end(self) -> float:
        return self.shots()[-1]["t1"]

    def graphics(self) -> list[dict]:
        out = []
        for g in self.data.get("graphics", []):
            item = dict(g)
            item["start"] = self.time(g["start"])
            item["end"] = self.time(g["end"])
            out.append(item)
        return out

    def summary(self) -> dict[str, Any]:
        shots = self.shots()
        return {
            "title": self.data.get("title", self.root.name),
            "length_s": round(shots[-1]["t1"], 2), "frames": frame(shots[-1]["t1"]),
            "anchors": {k: round(v, 2) for k, v in self.anchors.items()},
            "shots": [{"name": s["name"], "start": round(s["t0"], 2), "end": round(s["t1"], 2),
                       "record_frame": frame(s["t0"]), "frames": frame(s["t1"]) - frame(s["t0"]),
                       "layers": len(s["layers"]), "note": s["note"]} for s in shots],
            "graphics": len(self.data.get("graphics", [])),
        }
