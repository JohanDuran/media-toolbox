"""Map per-file WhisperX transcripts onto a Resolve timeline (runs inside the gateway).

Input (inside project_dir):
  timeline/<timeline>.audio1.json   Audio 1 items (record + source frames),
                                    captured from Resolve's timeline probe_timeline_structure
  transcripts/<stem>.json           output of the transcribe tool

Output (in project_dir/transcripts/):
  <timeline>.timeline.json   words + segments in TIMELINE time: record frame, timecode,
                             seconds from timeline start; cut-out parts are dropped
  <timeline>.timeline.srt    subtitles aligned to the edit (timeline starts at 00:00:00)
  <timeline>.timeline.txt    readable transcript with timecodes, one line per segment
"""

import json
from pathlib import Path


def timecode(frame: int, fps: int) -> str:
    s, f = divmod(frame, fps)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02}:{m:02}:{s:02}:{f:02}"


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def timeline_transcript(p: dict, log) -> dict:
    proj = Path(p["project_dir"])
    name = p.get("timeline") or "Timeline 1"
    gap = float(p.get("gap", 0.8))
    layout = json.loads((proj / "timeline" / f"{name}.audio1.json").read_text(encoding="utf-8"))
    fps, t0 = layout["fps"], layout["start_frame"]
    items = [dict(zip(layout["item_fields"], row)) for row in layout["items"]]

    transcripts = {}
    for stem in {it["source_file_stem"] for it in items}:
        transcripts[stem] = json.loads((proj / "transcripts" / f"{stem}.json").read_text(encoding="utf-8"))

    words = []
    for idx, it in enumerate(items):
        src_in = it["source_start"] / fps
        src_out = it["source_end_exclusive"] / fps
        offset = it["record_start"] - it["source_start"]  # record frame = source frame + offset
        for seg in transcripts[it["source_file_stem"]]["segments"]:
            for w in seg["words"]:
                # Keep a word only if its midpoint is inside this item's source range.
                if "start" not in w or not (src_in <= (w["start"] + w["end"]) / 2 < src_out):
                    continue
                rec_start = round(max(w["start"], src_in) * fps) + offset
                rec_end = round(min(w["end"], src_out) * fps) + offset
                words.append({
                    "word": w["word"], "record_start": rec_start, "record_end": rec_end,
                    "timecode": timecode(rec_start, fps),
                    "t": round((rec_start - t0) / fps, 3), "t_end": round((rec_end - t0) / fps, 3),
                    "score": w.get("score"), "item_index": idx,
                    "source_file": it["source_file_stem"], "source_t": w["start"],
                })
    words.sort(key=lambda w: w["record_start"])

    # Regroup into lines: break at item boundaries, long pauses, or sentence ends.
    lines, cur = [], []
    for w in words:
        if cur and (w["item_index"] != cur[-1]["item_index"] or w["t"] - cur[-1]["t_end"] > gap
                    or cur[-1]["word"].rstrip().endswith((".", "?", "!"))):
            lines.append(cur)
            cur = []
        cur.append(w)
    if cur:
        lines.append(cur)
    segments = [{
        "text": " ".join(w["word"] for w in ln),
        "record_start": ln[0]["record_start"], "record_end": ln[-1]["record_end"],
        "timecode": ln[0]["timecode"], "t": ln[0]["t"], "t_end": ln[-1]["t_end"],
        "item_index": ln[0]["item_index"],
    } for ln in lines]

    out = proj / "transcripts"
    js, srt, txt = out / f"{name}.timeline.json", out / f"{name}.timeline.srt", out / f"{name}.timeline.txt"
    js.write_text(json.dumps({
        "timeline": name, "fps": fps, "start_frame": t0, "start_timecode": layout["start_timecode"],
        "note": "record_* = absolute timeline frames; t = seconds from timeline start",
        "segments": segments, "words": words,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    with srt.open("w", encoding="utf-8", newline="\r\n") as f:  # Windows line endings, as before
        for i, s in enumerate(segments, 1):
            f.write(f"{i}\n{srt_time(s['t'])} --> {srt_time(s['t_end'])}\n{s['text']}\n\n")
    with txt.open("w", encoding="utf-8", newline="\r\n") as f:
        for s in segments:
            f.write(f"[{s['timecode']}] {s['text']}\n")

    dur = (items[-1]["record_end"] - t0) / fps
    log(f"{len(words)} words, {len(segments)} lines over {dur:.0f}s")
    return {"words": len(words), "lines": len(segments), "duration_s": round(dur, 1),
            "outputs": [str(js), str(srt), str(txt)]}
