# Media toolbox

Free, local media tools in Docker, behind one gateway that you open in the browser and Claude calls
as an MCP server, from any project.

| Tool | Worker | Runs on | What it does |
|---|---|---|---|
| `transcribe` | whisperx | GPU | WhisperX transcripts with word timings (.json, .srt, .words.tsv) |
| `timeline_transcript` | gateway | CPU | Maps per-file transcripts onto a Resolve timeline |
| `sfx_generate` + sound library | stable-audio | GPU | Stable Audio Open 1.0 SFX / music beds, cataloged in the shared library |
| `speak` | tts | GPU | Kokoro-82M text to speech (English, Spanish, French, Italian, Portuguese, Hindi) |
| `render_hyperframes` | render | CPU | Local HyperFrames render, pinned HyperFrames + Chrome |
| `ffmpeg`, `ffprobe` | render | CPU | Any ffmpeg command, media info |
| `image_depth`, `image_cutout` | vision | GPU | Depth maps (Depth Anything V2 Small) and subject cutouts + background plates (BiRefNet) for stills |
| `parallax` | render | CPU | One still → a moving 2.5D camera shot (push-in, pull-out, pans, drift), from the cutout + plate or the depth map. See [Stills to motion](#stills-to-motion) |
| `short_info`, `short_build`, `short_preview`, `short_frames` | render | CPU | Vertical Shorts (1080×1920) built from a `short.json` spec: shots from screen recordings, graphics + word-by-word captions, voice and music/SFX, all timed to the narration's words. See [docs/short-spec.md](docs/short-spec.md) |

Dashboard: **http://localhost:8765**. MCP: **http://localhost:8765/mcp**.

## Architecture

```
 browser ──► http://localhost:8765 ◄── Claude (MCP, any project)
                     │
              ┌──────┴──────┐  gateway: dashboard (React) · REST /api · MCP /mcp
              │   gateway   │  job queue (SQLite) · GPU lock · Windows path conversion
              └──┬───┬───┬──┴───┐
      whisperx  stable-audio  tts  vision  render   one image per tool, internal network only
                     │
   HOST_HOME (e.g. C:\Users\you) → /host   (inputs and outputs, normal Windows paths in and out)
   HF / torch caches under HOST_HOME\.cache   (models downloaded once, shared)
```

- **One job per worker, one GPU job at a time.** Before a GPU job starts, the other GPU workers are
  asked to unload their models, so an 8 GB card never runs out of VRAM. A model stays loaded between
  jobs of the same tool, so the second transcription starts instantly.
- **Paths:** pass normal Windows paths under `HOST_HOME`; the gateway converts them. Anything
  outside that folder is refused with a clear message.
- **Protected folders:** nothing is ever written into `PROTECTED_DIRS` (e.g. your raw recordings).
  Files there can still be read (e.g. as ffmpeg `-i` inputs).
- **Defaults:** outputs without an explicit folder go to `OUTPUTS_DIR\<tool>\`; generated sounds go
  into the shared sound library (`SFX_LIBRARY`).

## Folder layout

```
media-toolbox/
├── .env.example          settings template; install.ps1 turns it into .env (git-ignored)
├── install.ps1           one-step setup on Windows
├── docker-compose.yml    all services
├── docs/short-spec.md    the short.json format for vertical Shorts
├── gateway/
│   ├── app/              FastAPI: api, MCP tools (mcp_tools.py), queue (runner.py), tool specs (tools.py), shorts.py
│   └── web/              dashboard (React + TypeScript, built into the gateway image)
└── workers/
    ├── Dockerfile.gpu    one shared torch base + a target per GPU tool
    ├── Dockerfile.render HyperFrames + Chrome + ffmpeg
    ├── common/           the worker contract (toolbox_worker.py), the Shorts spec parser (shorts_spec.py),
    │                     the pinned torch build
    ├── render/           server.py + shorts_build.py (the Shorts engine), templates/parallax (HyperFrames)
    └── <tool>/           server.py + requirements.txt (pinned)
```

## Requirements

- Windows 10/11 with [Docker Desktop](https://www.docker.com/products/docker-desktop/) (WSL 2 backend).
- For the GPU tools (transcription, sound effects, text to speech): an NVIDIA GPU with a current
  driver (CUDA 12.8; RTX 50 series supported). 8 GB of VRAM is enough. Without one, ffmpeg,
  HyperFrames renders and Shorts still work.
- About 40 GB of disk for the images and models.

## Quick install

```bash
git clone https://github.com/JohanDuran/media-toolbox
```

```bash
powershell -ExecutionPolicy Bypass -File media-toolbox\install.ps1
```

`install.ps1` checks Docker and your GPU, creates your studio folder (default
`%USERPROFILE%\media-studio`, change it with `-StudioDir`), writes `.env`, pulls the prebuilt images from
`ghcr.io` and starts everything. Options: `-RecordingsDir` (your raw recordings, kept read-only; default `media-studio\recordings`),
`-NoGpu`, `-Build` (build the images yourself), `-Force` (rewrite `.env`). It's safe to run again.

Sound effects use Stable Audio Open, which is gated on Hugging Face: accept its license on the
[model page](https://huggingface.co/stabilityai/stable-audio-open-1.0) and run `hf auth login` once
on the PC. The token saved under `%USERPROFILE%\.cache\huggingface` is used. The other tools need no account.

Then open **http://localhost:8765**.

## Use with Claude

The easiest way is the **video-studio** Claude Code plugin. It brings the MCP connection, the
skills (Shorts, editing recordings, explainers) and a `/studio-setup` command. Install it from a terminal
(PowerShell, or the terminal panel in the Claude desktop app):

```bash
claude plugin marketplace add JohanDuran/video-studio
```

```bash
claude plugin install video-studio@video-studio
```

Then start a **new** Claude Code session (plugins load when a session starts) and run `/studio-setup`.

> **Claude desktop app:** typing `/plugin marketplace add …` in a chat opens the plugin manager window instead of
> running the command, so use the terminal commands above. In the Claude Code CLI, the same steps also work as
> `/plugin marketplace add JohanDuran/video-studio` and `/plugin install video-studio@video-studio`.

Or register only the MCP server, once, for every project:

```bash
claude mcp add --scope user --transport http media-toolbox http://localhost:8765/mcp
```

Job tools wait for the result; a long job returns its id, and `wait_for_job` picks it up.

## Everyday commands

Run these in this folder.

```bash
docker compose up -d
```

```bash
docker compose ps
```

```bash
docker compose logs -f gateway
```

Update to the latest images:

```bash
docker compose pull
```

```bash
docker compose up -d
```

Stop everything (the containers start again with Docker Desktop otherwise):

```bash
docker compose down
```

## Advanced

**Settings** are in `.env` (template: `.env.example`): your user folder, port, studio paths,
protected folders, `COMPOSE_PROFILES` (`gpu` or empty), `TOOLBOX_VERSION` (image tag) and
`TRANSCRIBE_VOCAB` (names and terms transcription should recognize).

**Without an NVIDIA GPU:** set `COMPOSE_PROFILES=` (empty). The GPU workers don't start; jobs for
them fail after two minutes with a message saying why.

**Build the images yourself** (after changing code; the first build downloads ~10 GB):

```bash
docker compose up -d --build
```

Rebuild one tool without touching the others (rebuilding the gateway without `--no-deps`
re-exports the large GPU images):

```bash
docker compose up -d --build --no-deps whisperx
```

**Publishing images:** pushing a tag like `v0.2.0` runs `.github/workflows/images.yml`, which
builds all five images and pushes them to `ghcr.io/johanduran/media-toolbox-*` as that version
and `latest`.

## Using it from scripts

```bash
curl -s -X POST localhost:8765/api/jobs -H "Content-Type: application/json" -d "{\"tool\":\"ffprobe\",\"params\":{\"path\":\"C:\\\\Users\\\\you\\\\Videos\\\\clip.mp4\"}}"
```

Then `GET /api/jobs/<id>` (or `POST /api/jobs/<id>/wait`). `GET /api/tools` lists every tool's parameters.

## Stills to motion

Turn a still (a listing photo, a screenshot, a generated image) into a moving shot:

1. `image_depth` → `<stem>_depth.png` in a `stills` folder next to the image (edges softened by default so the
   motion bends instead of tearing). For layers mode, `image_cutout` with `plate: true` → `<stem>_cut.png`,
   `<stem>_mask.png`, `<stem>_plate.png`. Pass a whole Short's stills at once: they share one model load.
2. `parallax` on the original image. It finds those files by name.
   - `mode: depth` (default) moves every pixel by its depth: a real 3D feel on photos of places (houses,
     streets, rooms). Keep `strength` near 1; higher values stretch the edges between near and far.
   - `mode: layers` slides the cutout over the filled-in background. Only for one clean, fully cut-out
     subject (a person, a product on a plain background): on a house photo the cutout leaves bushes and
     leaves behind, and the filled background is smeary.
   - `move`: push-in, pull-out, pan-left/right/up/down, drift. `size`: `1080x1920` (default), `1920x1080`,
     any `WxH`, or `source`. A landscape photo in a vertical frame is cover-cropped around `center`.

Renders run in the render worker's Chrome with a software GPU: about 25 s for 3 s of 1080×1920.

## Adding a tool

1. `workers/<tool>/server.py`: handlers built with `toolbox_worker.create_app` (same contract as the others).
2. A Dockerfile target (or its own Dockerfile) and a service in `docker-compose.yml`.
3. The worker in `WORKERS` (`gateway/app/config.py`), a `Tool` spec in `gateway/app/tools.py`, and an
   MCP function in `gateway/app/mcp_tools.py`. The dashboard picks the form up automatically.

## License

The toolbox code is MIT (see [LICENSE](LICENSE)). The models it downloads have their own licenses:

### Models

- WhisperX / faster-whisper large-v3: MIT / Apache 2.0.
- Stable Audio Open 1.0: Stability AI Community License (free under $1M yearly revenue).
- Kokoro-82M: Apache 2.0.
- Depth Anything V2 **Small**: Apache 2.0. (The Base and Large versions are non-commercial: don't swap them in.)
- BiRefNet: MIT.
