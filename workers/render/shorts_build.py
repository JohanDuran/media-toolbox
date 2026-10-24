"""Build vertical Shorts (1080x1920) from a short.json spec, inside the render worker.

  build(spec, parts, only)  shots (V1 clips) + graphics overlay (V2, transparent ProRes 4444)
                            + narration (A1) + music/SFX bed (A2); returns the Resolve placement
  preview(spec)             all of it composited with sound into one MP4, plus a contact sheet
  frames(src, times, ...)   a labelled grid of frames from a recording, for planning crops/moments

Each shot is one or more layers (crops of screen recordings or stills) over a dark backdrop,
with eased zoom/pan keys and privacy blurs. Fonts come from the Windows font folder mounted at
FONTS_DIR (read-only), so graphics match what the same text looks like on the PC. Without
Segoe UI there (another OS, or no fonts mounted), the open Noto Sans in the image is used.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from shorts_spec import Spec, ass_color, frame

W, H, FPS = 1080, 1920, 30
FONTS = os.environ.get("FONTS_DIR", "/fonts")
_HAS_SEGOE = Path(FONTS, "segoeuib.ttf").is_file()
if _HAS_SEGOE:
    BOLD_FONT = f"{FONTS}/segoeuib.ttf"
    HEAVY, HEAVY_BOLD, REGULAR = "Segoe UI Black", "0", "Segoe UI"
else:  # open fallback shipped in the image (fonts-noto-core)
    BOLD_FONT = "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"
    HEAVY, HEAVY_BOLD, REGULAR = "Noto Sans", "-1", "Noto Sans"
POP = r"\fscx70\fscy70\t(0,140,\fscx108\fscy108)\t(140,220,\fscx100\fscy100)"


def ff(*args: str, cwd: Path | None = None) -> None:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
                       cwd=cwd, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"ffmpeg failed: {p.stderr.strip()[-1500:]}")


# --- shots -----------------------------------------------------------------------------------

def _ease(a: float, b: float, ta: float, tb: float) -> str:
    s = f"clip((it-{ta:.3f})/{max(tb - ta, 1e-3):.3f},0,1)"
    return f"({a}+({b}-({a}))*({s})*({s})*(3-2*({s})))"


def _keyed(keys, idx: int) -> str:
    expr = str(keys[-1][idx])
    for (ta, *va), (tb, *vb) in reversed(list(zip(keys, keys[1:]))):
        expr = f"if(lt(it,{tb:.3f}),{_ease(va[idx - 1], vb[idx - 1], ta, tb)},{expr})"
    return expr


def make_backdrop(path: Path, c0: str, c1: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ff("-f", "lavfi", "-i", f"gradients=s={W}x{H}:c0={c0}:c1={c1}:x0=540:y0=700:x1=540:y1=1920:"
       "nb_colors=2:type=radial:d=1:r=1", "-frames:v", "1", str(path))
    return path


def build_shot(shot: dict, out_dir: Path, bg: Path) -> Path:
    n = frame(shot["t1"]) - frame(shot["t0"])
    dur = n / FPS
    inputs, chains, cards = [], [], []
    for i, L in enumerate(shot["layers"]):
        x, y, w, h = L["reg"]
        if L["still"]:
            inputs += ["-loop", "1", "-t", f"{dur + 0.5:.3f}", "-i", str(L["src"])]
        else:
            inputs += ["-ss", f"{L['ss']:.3f}", "-t", f"{dur * L['speed'] + 0.5:.3f}", "-i", str(L["src"])]
        cw = L["card_w"]
        ch = round(cw * h / w / 2) * 2
        keys = L["keys"] or [(0, 1.0, w / 2, h / 2)]
        z, fx, fy = _keyed(keys, 1), _keyed(keys, 2), _keyed(keys, 3)
        blur = "".join(f",delogo=x={bx - x}:y={by - y}:w={bw}:h={bh}" for bx, by, bw, bh in L["blur"])
        zp = (f"zoompan=z='{z}':x='clip(2*({fx})-iw/zoom/2,0,iw-iw/zoom)':"
              f"y='clip(2*({fy})-ih/zoom/2,0,ih-ih/zoom)':d=1:s={cw}x{ch}:fps={FPS}")
        chains.append(
            f"[{i}:v]crop={w}:{h}:{x}:{y}{blur},setpts=(PTS-STARTPTS)/{L['speed']},fps={FPS},"
            f"scale={2 * w}:{2 * h}:flags=lanczos,{zp}"
            + (",boxblur=6:1,eq=brightness=-0.28" if L["dim"] else "")
            + f",drawbox=x=0:y=0:w=iw:h=ih:color=white@0.12:t=3[c{i}]")
        cards.append((f"[c{i}]", round((W - cw) / 2), round(L["cy"] - ch / 2)))
    b = len(shot["layers"])
    inputs += ["-loop", "1", "-i", str(bg)]
    graph = chains + [f"[{b}:v]format=yuv420p[v0]"]
    for k, (label, ox, oy) in enumerate(cards):
        graph.append(f"[v{k}]{label}overlay={ox}:{oy}:shortest=1[v{k + 1}]")
    graph.append(f"[v{len(cards)}]format=yuv420p[out]")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{shot['name']}.mp4"
    ff(*inputs, "-filter_complex", ";".join(graph), "-map", "[out]", "-frames:v", str(n), "-r", str(FPS),
       "-c:v", "libx264", "-preset", "medium", "-crf", "14", "-pix_fmt", "yuv420p", str(out))
    thumbs = out_dir / "thumbs"
    thumbs.mkdir(exist_ok=True)
    ff("-ss", f"{dur / 2:.3f}", "-i", str(out), "-frames:v", "1", "-vf", "scale=216:384", str(thumbs / f"{shot['name']}.jpg"))
    return out


# --- graphics (ASS -> transparent ProRes 4444) -------------------------------------------------

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,@HEAVY@,82,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,@B@,0,0,0,100,100,0,0,1,7,3,5,40,40,0,1
Style: Head,@HEAVY@,66,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,@B@,0,0,0,100,100,1,0,1,6,3,5,40,40,0,1
Style: Big,@HEAVY@,168,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,@B@,0,0,0,100,100,0,0,1,9,4,5,40,40,0,1
Style: Tag,@HEAVY@,54,&H00FFFFFF,&H00FFFFFF,&H004D4DFF,&H00000000,@B@,0,0,0,100,100,2,0,3,16,0,5,40,40,0,1
Style: Card,@REGULAR@,50,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Shape,@REGULAR@,20,&H00241F1E,&H00241F1E,&H005777D9,&H00000000,0,0,0,0,100,100,0,0,1,3,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""".replace("@HEAVY@", HEAVY).replace("@B@", HEAVY_BOLD).replace("@REGULAR@", REGULAR)


def _ts(t: float) -> str:
    cs = max(0, round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def _rounded_rect(w: int, h: int, r: int) -> str:
    return (f"m {r} 0 l {w - r} 0 b {w} 0 {w} 0 {w} {r} l {w} {h - r} b {w} {h} {w} {h} {w - r} {h} "
            f"l {r} {h} b 0 {h} 0 {h} 0 {h - r} l 0 {r} b 0 0 0 0 {r} 0")


def graphic_event(g: dict) -> str:
    """One spec graphic -> one ASS Dialogue line."""
    tags = []
    if "pos" in g:
        tags.append(rf"\pos({int(g['pos'][0])},{int(g['pos'][1])})")
    if "fs" in g:
        tags.append(rf"\fs{int(g['fs'])}")
    if "bold" in g:
        tags.append(rf"\b{1 if g['bold'] else 0}")
    if "color" in g:
        tags.append(rf"\c{ass_color(g['color'])}")
    if "box" in g:  # Tag style: the box colour
        tags.append(rf"\3c{ass_color(g['box'])}")
    if g.get("strike"):
        tags.append(r"\s1")
    if g.get("pop", True):
        tags.append(POP)
    if "fade" in g:
        tags.append(rf"\fad({int(g['fade'][0])},{int(g['fade'][1])})")
    style = g.get("style", "Head")
    if "shape" in g:  # rounded rectangle card; pos = its top-left corner
        sh = g["shape"]
        tags += [rf"\1c{ass_color(sh.get('fill', 'card'))}", rf"\3c{ass_color(sh.get('border', 'white'))}",
                 rf"\bord{int(sh.get('bord', 4))}", r"\p1"]
        body = _rounded_rect(int(sh["w"]), int(sh["h"]), int(sh.get("r", 36))) + r"{\p0}"
        style = "Shape"
    elif "runs" in g:  # several colours in one line: [["CHAT ", "blue"], ["vs ", "white"], ...]
        body = "".join(rf"{{\c{ass_color(c)}}}{t}" for t, c in g["runs"])
    else:
        body = str(g.get("text", ""))
    layer = int(g.get("layer", 2 if style == "Shape" else 3 if style == "Tag" else 1))
    return f"Dialogue: {layer},{_ts(g['start'])},{_ts(g['end'])},{style},,0,0,0,,{{{''.join(tags)}}}{body}"


def caption_events(words: list[dict], end: float, y: int = 1480, max_words: int = 3) -> list[str]:
    yellow, white = ass_color("yellow"), ass_color("white")
    groups, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) == max_words or w["w"][-1] in ".,?!":
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    lines = []
    for gi, g in enumerate(groups):
        g_end = groups[gi + 1][0]["s"] if gi + 1 < len(groups) else end
        for wi, w in enumerate(g):
            t0 = g[0]["s"] if wi == 0 else w["s"]
            t1 = g[wi + 1]["s"] if wi + 1 < len(g) else g_end
            parts = [rf"{{\c{yellow}}}{x['w'].upper().rstrip('.,')}{{\c{white}}}" if k == wi
                     else x["w"].upper().rstrip(".,") for k, x in enumerate(g)]
            pop = r"\fscx85\fscy85\t(0,90,\fscx100\fscy100)" if wi == 0 else ""
            lines.append(f"Dialogue: 5,{_ts(t0)},{_ts(t1)},Cap,,0,0,0,,{{\\pos(540,{y}){pop}}}" + " ".join(parts))
    return lines


def build_overlay(spec: Spec, end: float) -> Path:
    events = [graphic_event(g) for g in spec.graphics()]
    cap = spec.data.get("captions", {})
    if cap.get("enabled", True):
        events += caption_events(spec.words, end, int(cap.get("y", 1480)), int(cap.get("max_words", 3)))
    ass = spec.out("overlay_ass", "edit/overlay.ass")
    mov = spec.out("overlay", "edit/overlay.mov")
    ass.parent.mkdir(parents=True, exist_ok=True)
    ass.write_text(ASS_HEADER + "\n".join(events) + "\n", encoding="utf-8-sig")
    ff("-f", "lavfi", "-i", f"color=c=black@0.0:s={W}x{H}:r={FPS}:d={end:.3f},format=rgba",
       "-vf", f"subtitles={ass.name}:fontsdir={FONTS}:alpha=1",
       "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-vendor", "apl0",
       str(mov), cwd=ass.parent)
    return mov


# --- audio -----------------------------------------------------------------------------------

def build_narration(spec: Spec, end: float) -> Path:
    a = spec.data.get("audio", {})
    src = spec.file(a.get("narration", "audio/narration.wav"))
    out = spec.out("narration", "audio/narration_final.wav")
    ff("-i", str(src), "-af", f"loudnorm=I=-15:TP=-1.5:LRA=7,volume=1.5dB,alimiter=limit=0.84,"
       f"aresample=48000,apad=whole_dur={end:.3f},atrim=0:{end:.3f}", "-ac", "2", "-c:a", "pcm_s24le", str(out))
    return out


def build_bed(spec: Spec, end: float) -> Path | None:
    a = spec.data.get("audio", {})
    if not a.get("music") and not a.get("sfx"):
        return None
    out = spec.out("bed", "audio/bed.wav")
    inputs, chains, labels = [], [], []
    if a.get("music"):
        inputs += ["-stream_loop", "-1", "-i", str(spec.file(a["music"]))]
        chains.append(f"[0]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{end:.3f},afade=t=in:d=0.05,"
                      f"afade=t=out:st={end - 1.3:.2f}:d=1.3,volume={float(a.get('music_db', -22))}dB[m]")
        labels.append("[m]")
    else:
        inputs += ["-f", "lavfi", "-t", f"{end:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        chains.append("[0]anull[m]")
        labels.append("[m]")
    for i, s in enumerate(a.get("sfx", []), start=1):
        times = [spec.time(x) for x in s["at"]]
        inputs += ["-i", str(spec.file(s["file"]))]
        chains.append(f"[{i}]aresample=48000,aformat=channel_layouts=stereo,asplit={len(times)}"
                      + "".join(f"[s{i}_{k}]" for k in range(len(times))))
        for k, t in enumerate(times):
            ms = max(0, round((t - float(s.get("peak", 0))) * 1000))
            chains.append(f"[s{i}_{k}]adelay={ms}|{ms},volume={float(s.get('gain', -10))}dB[o{i}_{k}]")
            labels.append(f"[o{i}_{k}]")
    chains.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=first,"
                  f"apad=whole_dur={end:.3f},atrim=0:{end:.3f},alimiter=limit=0.89[out]")
    ff(*inputs, "-filter_complex", ";".join(chains), "-map", "[out]", "-c:a", "pcm_s24le", str(out))
    return out


# --- entry points ----------------------------------------------------------------------------

def build(spec_path: Path, parts: list[str], only: list[str], log) -> dict:
    spec = Spec(spec_path)
    shots = spec.shots()
    end = shots[-1]["t1"]
    bd = spec.data.get("backdrop", {})
    if bd.get("image"):  # a ready-made 1080x1920 backdrop
        bg = spec.file(bd["image"])
        if not bg.exists():
            raise FileNotFoundError(f"backdrop image not found: {bg}")
    else:
        bg = make_backdrop(spec.out("backdrop", "edit/bg.png"), bd.get("c0", "0x16203a"), bd.get("c1", "0x06080d"))
    shots_dir = spec.out("shots", "edit/shots")
    result: dict = {"length_s": round(end, 3), "frames": frame(end), "built": []}
    if "shots" in parts:
        unknown = set(only) - {s["name"] for s in shots}
        if unknown:
            raise ValueError(f"no such shot(s): {sorted(unknown)}")
        for s in shots:
            if only and s["name"] not in only:
                continue
            p = build_shot(s, shots_dir, bg)
            log(f"built {p.name} ({frame(s['t1']) - frame(s['t0'])} frames)")
            result["built"].append(str(p))
    if "overlay" in parts:
        result["overlay"] = str(build_overlay(spec, end))
        log("built overlay")
    if "audio" in parts:
        result["narration"] = str(build_narration(spec, end))
        bed = build_bed(spec, end)
        result["bed"] = str(bed) if bed else None
        log("built narration" + (" + bed" if bed else ""))
    # Where everything goes in Resolve: V1 shots back to back, V2 overlay, A1 voice, A2 bed.
    result["timeline"] = [{"track": "V1", "file": str(shots_dir / f"{s['name']}.mp4"),
                           "record_frame": frame(s["t0"]), "frames": frame(s["t1"]) - frame(s["t0"])}
                          for s in shots]
    result["timeline"] += [{"track": tr, "file": str(f), "record_frame": 0, "frames": frame(end)} for tr, f in
                           (("V2", spec.out("overlay", "edit/overlay.mov")),
                            ("A1", spec.out("narration", "audio/narration_final.wav")),
                            ("A2", spec.out("bed", "audio/bed.wav")))]
    return result


def preview(spec_path: Path, log) -> dict:
    """Composite the built pieces into one MP4 with sound, plus a one-frame-per-shot contact sheet."""
    spec = Spec(spec_path)
    shots = spec.shots()
    end = shots[-1]["t1"]
    shots_dir = spec.out("shots", "edit/shots")
    missing = [s["name"] for s in shots if not (shots_dir / f"{s['name']}.mp4").exists()]
    if missing:
        raise FileNotFoundError(f"build these shots first: {missing}")
    work = spec.out("preview", "edit/preview.mp4").parent
    work.mkdir(parents=True, exist_ok=True)
    lst = work / "preview_shots.txt"
    lst.write_text("".join(f"file '{(shots_dir / (s['name'] + '.mp4')).as_posix()}'\n" for s in shots))
    inputs = ["-f", "concat", "-safe", "0", "-i", str(lst)]
    overlay, narr, bed = (spec.out("overlay", "edit/overlay.mov"), spec.out("narration", "audio/narration_final.wav"),
                          spec.out("bed", "audio/bed.wav"))
    graph, amaps = "[0:v]null[v]", []
    k = 1
    if overlay.exists():
        inputs += ["-i", str(overlay)]
        graph = f"[0:v][{k}:v]overlay=0:0:format=auto[v]"
        k += 1
    for f in (narr, bed):
        if f.exists():
            inputs += ["-i", str(f)]
            amaps.append(f"[{k}:a]")
            k += 1
    if amaps:
        graph += f";{''.join(amaps)}amix=inputs={len(amaps)}:normalize=0[a]"
    out = spec.out("preview", "edit/preview.mp4")
    ff(*inputs, "-filter_complex", graph, "-map", "[v]", *(["-map", "[a]"] if amaps else []),
       "-t", f"{end:.3f}", "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
       "-movflags", "+faststart", str(out))
    log(f"built {out.name}")
    sheet = out.with_name("preview_sheet.jpg")
    tiles = []
    for i, s in enumerate(shots):
        t = (s["t0"] + s["t1"]) / 2
        tile = work / f".tile_{i:02}.png"
        label = f"{s['name']}  {s['t0']:.1f}-{s['t1']:.1f}s".replace(":", r"\:")
        ff("-ss", f"{t:.3f}", "-i", str(out), "-frames:v", "1", "-vf",
           f"scale=270:480,pad=270:520:0:40:color=0x1b1f27,drawtext=fontfile={BOLD_FONT}:text='{label}':"
           f"x=(w-tw)/2:y=12:fontsize=16:fontcolor=white", str(tile))
        tiles.append(tile)
    cols = min(7, len(tiles))
    rows = -(-len(tiles) // cols)
    ff("-framerate", "1", "-i", str(work / ".tile_%02d.png"), "-vf", f"tile={cols}x{rows}:color=0x1b1f27",
       "-frames:v", "1", str(sheet))
    for tile in tiles:
        tile.unlink(missing_ok=True)
    return {"preview": str(out), "sheet": str(sheet), "length_s": round(end, 3)}


def frames(src: Path, times: list[float], out: Path, region: list[int] | None = None, cols: int = 4) -> dict:
    """A grid of frames (each labelled with its time) from a recording, to plan crops and moments."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tiles = []
    for i, t in enumerate(times):
        tile = out.parent / f".frame_{i:03}.png"
        crop = f"crop={region[2]}:{region[3]}:{region[0]}:{region[1]}," if region else ""
        ff("-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1", "-vf",
           f"{crop}scale=640:-2,drawtext=fontfile={BOLD_FONT}:text='{t:.2f}s':x=8:y=8:fontsize=26:"
           f"fontcolor=yellow:box=1:boxcolor=black@0.7", str(tile))
        tiles.append(tile)
    cols = min(cols, len(tiles))
    rows = -(-len(tiles) // cols)
    ff("-framerate", "1", "-i", str(out.parent / ".frame_%03d.png"), "-vf", f"tile={cols}x{rows}",
       "-frames:v", "1", str(out))
    for tile in tiles:
        tile.unlink(missing_ok=True)
    return {"sheet": str(out), "count": len(times)}
