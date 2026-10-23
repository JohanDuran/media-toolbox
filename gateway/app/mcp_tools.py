"""MCP tools: the toolbox as Claude sees it. Each one is a thin call into the same
runner the dashboard and REST API use.

Job tools wait for the result (up to wait_seconds). If a job takes longer, they return
its id and status; follow up with wait_for_job / job_status.
"""

from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import FastMCP

from . import library, shorts
from .config import PUBLIC_URL, SFX_LIBRARY
from .paths import PathError, windows
from .runner import Runner

INSTRUCTIONS = f"""Local media toolbox (free, runs on this PC's GPU in Docker). Pass normal Windows paths;
anything under the user's home folder works. Outputs never go into the OBS folder.
Tools: transcribe (WhisperX), timeline_transcript, sfx_search / sfx_generate / sfx_mark / sfx_scan
(Stable Audio + shared sound library at {windows(SFX_LIBRARY)}), speak (Kokoro TTS),
render_hyperframes, ffmpeg, ffprobe, vertical Shorts from a short.json spec (short_info / short_build /
short_preview / short_frames), plus job_status / wait_for_job / list_jobs / cancel_job.
Search the sound library before generating. Dashboard: {PUBLIC_URL}"""


def _summary(job: dict, log_lines: int = 15) -> dict:
    out = {"job_id": job["id"], "tool": job["tool"], "status": job["status"]}
    if job.get("progress") is not None and job["status"] == "running":
        out["progress"] = round(job["progress"], 2)
    if job.get("result") is not None:
        out["result"] = job["result"]
    if job.get("error"):
        out["error"] = job["error"]
    if job["status"] in ("queued", "running"):
        out["note"] = "Still in progress: call wait_for_job with this job_id."
    if log_lines and job.get("logs") is not None and (job["status"] != "done" or log_lines > 15):
        out["log_tail"] = job["logs"].strip().splitlines()[-log_lines:]
    out["dashboard"] = f"{PUBLIC_URL}/#/jobs/{job['id']}"
    return out


def build_mcp(runner: Runner) -> FastMCP:
    mcp = FastMCP("media-toolbox", instructions=INSTRUCTIONS, stateless_http=True)

    async def run(tool: str, params: dict, wait_seconds: float) -> dict:
        params = {k: v for k, v in params.items() if v is not None}
        try:
            job = runner.submit(tool, params, source="mcp")
        except (ValueError, PathError) as e:
            return {"error": str(e)}
        job = await runner.wait(job["id"], max(0.0, wait_seconds))
        return _summary(job)

    @mcp.tool()
    async def toolbox_status() -> dict:
        """Workers (up / busy / model loaded), GPU memory and job counts."""
        s = runner.state()
        return {
            "workers": [{k: w[k] for k in ("name", "up", "busy", "job_id", "model_loaded", "error")}
                        for w in s["workers"]],
            "gpu": s["gpu"], "jobs": s["counts"], "dashboard": PUBLIC_URL,
        }

    @mcp.tool()
    async def transcribe(files: list[str], out_dir: str | None = None, language: str | None = None,
                         model: str = "large-v3", stream: int = 0, vocab: str | None = None,
                         wait_seconds: float = 900) -> dict:
        """Transcribe video/audio files with WhisperX (word timings). Writes <name>.json, .srt and
        .words.tsv per file into out_dir (default: toolbox outputs\\transcripts). stream 0 = OBS mic track.
        Times are seconds in the source file. vocab biases recognition toward names/terms."""
        return await run("transcribe", {"files": files, "out_dir": out_dir, "language": language, "model": model,
                                        "stream": stream, "vocab": vocab}, wait_seconds)

    @mcp.tool()
    async def timeline_transcript(project_dir: str, timeline: str = "Timeline 1", gap: float = 0.8) -> dict:
        """Map per-file transcripts onto a Resolve timeline. Needs <project_dir>\\timeline\\<timeline>.audio1.json
        (Audio 1 items captured from Resolve) and <project_dir>\\transcripts\\<stem>.json; writes
        <timeline>.timeline.json/.srt/.txt with timeline frames, timecodes and seconds."""
        return await run("timeline_transcript", {"project_dir": project_dir, "timeline": timeline, "gap": gap}, 120)

    @mcp.tool()
    async def sfx_search(words: list[str] | None = None,
                         status: Literal["new", "keep", "reject"] | None = None) -> dict:
        """Search the shared sound library (all words must match the prompt, notes, tags or file name).
        Do this before generating: offer sounds the user already kept."""
        hits = library.search(words, status)
        return {"matches": len(hits), "entries": hits[:50], "library": windows(SFX_LIBRARY)}

    @mcp.tool()
    async def sfx_generate(prompt: str, seconds: float = 10, variations: int = 3, seed: int | None = None,
                           steps: int = 100, guidance: float = 7.0,
                           negative: str = "low quality, distortion, noise", out_dir: str | None = None,
                           wait_seconds: float = 600) -> dict:
        """Generate SFX / ambience / short music beds (max 47 s) with Stable Audio Open 1.0. Writes
        <slug>_s<seed>_<n>.wav (44.1 kHz stereo 24-bit) into the shared library and catalogs them, unless
        out_dir is given (one-off sound, not cataloged). Reuse a seed to tweak a result. Outputs can peak
        at 0 dB."""
        return await run("generate_sfx", {"prompt": prompt, "seconds": seconds, "variations": variations,
                                          "seed": seed, "steps": steps, "guidance": guidance,
                                          "negative": negative, "out_dir": out_dir}, wait_seconds)

    @mcp.tool()
    async def sfx_mark(file: str, status: Literal["keep", "reject", "new"] | None = None,
                       used_in: str | None = None, tags: list[str] | None = None, note: str | None = None) -> dict:
        """Record the user's verdict on library sounds: file name or glob (e.g. 'cow-*_2.wav'), keep/reject,
        the Resolve project that uses it, tags, a note. This is how the library learns their taste."""
        try:
            return {"updated": library.mark(file, status, used_in, tags, note)}
        except ValueError as e:
            return {"error": str(e)}

    @mcp.tool()
    async def sfx_scan() -> dict:
        """Catalog WAVs that were copied into the sound library by hand."""
        return library.scan()

    @mcp.tool()
    async def speak(text: str, voice: str = "af_heart", speed: float = 1.0, out_path: str | None = None,
                    pause: float = 0.35, wait_seconds: float = 600) -> dict:
        """Text to speech with Kokoro-82M (free, local). Writes a 24 kHz mono WAV. The voice's first letter
        picks the language: af_/am_ US English, bf_/bm_ UK English, ef_dora/em_alex/em_santa Spanish,
        ff_siwis French, if_sara/im_nicola Italian, pf_dora/pm_alex Portuguese, hf_alpha/hm_omega Hindi.
        Blank lines in text become short pauses."""
        return await run("speak", {"text": text, "voice": voice, "speed": speed, "out_path": out_path,
                                   "pause": pause}, wait_seconds)

    @mcp.tool()
    async def render_hyperframes(project_dir: str, output: str | None = None, composition: str | None = None,
                                 format: Literal["mp4", "mov", "webm", "gif", "png-sequence"] = "mp4",
                                 quality: Literal["draft", "looks", "delivery"] | None = None,
                                 fps: Literal["24", "30", "60"] | None = None, extra_args: list[str] | None = None,
                                 wait_seconds: float = 1800) -> dict:
        """Render a HyperFrames project locally (pinned HyperFrames + Chrome, never cloud). format mov carries
        alpha for overlays. Default output: <project>\\renders\\<name>_<time>.<ext>. extra_args are passed to
        `hyperframes render` (e.g. ["--variables", "{\\"title\\":\\"Hi\\"}"])."""
        return await run("render_hyperframes", {"project_dir": project_dir, "output": output,
                                                "composition": composition, "format": format, "quality": quality,
                                                "fps": fps, "extra_args": extra_args}, wait_seconds)

    @mcp.tool()
    async def ffmpeg(args: list[str], wait_seconds: float = 900) -> dict:
        """Run ffmpeg (one list item per argument; Windows paths are converted, but not paths inside filter
        strings). Inputs may be in the OBS folder, outputs may not. Add -y to overwrite. The log tail is
        returned, so analysis filters like volumedetect work."""
        res = await run("ffmpeg", {"args": args}, wait_seconds)
        if res.get("status") == "done":
            res["log_tail"] = res.pop("result", {}).get("log_tail")
        return res

    @mcp.tool()
    async def ffprobe(path: str) -> dict:
        """A media file's format and streams (codec, duration, resolution, audio tracks...)."""
        return await run("ffprobe", {"path": path}, 120)

    @mcp.tool()
    async def short_info(project_dir: str) -> dict:
        """Read a Shorts project's short.json: its length, every shot with start/end/record frame (as the
        narration currently times them), the graphics, and which outputs are already built. Instant."""
        try:
            return shorts.info(project_dir)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def short_build(project_dir: str, parts: list[str] | None = None, only: list[str] | None = None,
                          wait_seconds: float = 900) -> dict:
        """Build a vertical Short (1080x1920, 30 fps) from <project_dir>\\short.json: one MP4 per shot (V1),
        the graphics + word-by-word captions overlay (V2, ProRes 4444 with alpha), the narration leveled to
        about -14 LUFS (A1) and the music/SFX bed (A2). parts: any of shots, overlay, audio (default all);
        only: rebuild just these shots. The result's 'timeline' says where each file goes in Resolve
        (record_frame from the timeline start). Format of short.json: media-toolbox docs/short-spec.md."""
        return await run("short_build", {"project_dir": project_dir, "parts": parts, "only": only}, wait_seconds)

    @mcp.tool()
    async def short_preview(project_dir: str, wait_seconds: float = 600) -> dict:
        """Composite a built Short (shots + overlay + audio) into <project>\\edit\\preview.mp4 and a contact
        sheet with one frame per shot (preview_sheet.jpg), to check it before assembling in Resolve."""
        return await run("short_preview", {"project_dir": project_dir}, wait_seconds)

    @mcp.tool()
    async def short_frames(src: str, times: list[float], region: list[int] | None = None, cols: int = 4,
                           out: str | None = None) -> dict:
        """Grab frames from a screen recording at the given seconds into one labelled grid image (optionally
        cropped to region [x, y, w, h]), to plan a Short's crops and moments. Read the image afterwards."""
        return await run("short_frames", {"src": src, "times": [str(t) for t in times],
                                          "region": [str(v) for v in region] if region else None,
                                          "cols": cols, "out": out}, 300)

    @mcp.tool()
    async def job_status(job_id: str, log_lines: int = 40) -> dict:
        """Status, progress, result or error, and the last log lines of a job."""
        job = runner.db.get(job_id)
        return _summary(job, log_lines) if job else {"error": f"unknown job {job_id}"}

    @mcp.tool()
    async def wait_for_job(job_id: str, timeout_seconds: float = 900) -> dict:
        """Wait until a job finishes (or the timeout passes) and return its result."""
        try:
            return _summary(await runner.wait(job_id, timeout_seconds))
        except ValueError as e:
            return {"error": str(e)}

    @mcp.tool()
    async def list_jobs(limit: int = 20, status: str | None = None) -> dict:
        """Recent jobs, newest first (optionally only one status: queued, running, done, failed...)."""
        return {"jobs": [{k: j[k] for k in ("id", "tool", "status", "created", "error")}
                         for j in runner.db.list(limit, status)]}

    @mcp.tool()
    async def cancel_job(job_id: str) -> dict:
        """Cancel a queued job, or ask a running one to stop at its next checkpoint."""
        try:
            return _summary(await runner.cancel(job_id))
        except ValueError as e:
            return {"error": str(e)}

    return mcp
