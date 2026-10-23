# Media toolbox

Free, local media tools in Docker, behind one gateway that you open in the browser and Claude calls
as an MCP server, from any project.

| Tool | Worker | Runs on | What it does |
|---|---|---|---|
| `transcribe` | whisperx | GPU | WhisperX transcripts with word timings (.json, .srt, .words.tsv) |
| `timeline_transcript` | gateway | CPU | Maps per-file transcripts onto a Resolve timeline |
| `generate_sfx` + sound library | stable-audio | GPU | Stable Audio Open 1.0 SFX / music beds, cataloged in the shared library |
| `speak` | tts | GPU | Kokoro-82M text to speech (English, Spanish, French, Italian, Portuguese, Hindi) |
| `render_hyperframes` | render | CPU | Local HyperFrames render, pinned HyperFrames + Chrome |
| `ffmpeg`, `ffprobe` | render | CPU | Any ffmpeg command, media info |
| `short_info`, `short_build`, `short_preview`, `short_frames` | render | CPU | Vertical Shorts (1080×1920) built from a `short.json` spec: shots from screen recordings, graphics + word-by-word captions, voice and music/SFX, all timed to the narration's words. See [docs/short-spec.md](docs/short-spec.md) |

Dashboard: **http://localhost:8765**. MCP: **http://localhost:8765/mcp**.

## Architecture

```
 browser ──► http://localhost:8765 ◄── Claude (MCP, any project)
                     │
              ┌──────┴──────┐  gateway: dashboard (React) · REST /api · MCP /mcp
              │   gateway   │  job queue (SQLite) · GPU lock · Windows path conversion
              └──┬───┬───┬──┴───┐
          whisperx  stable-audio  tts  render       one image per tool, internal network only
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
├── .env.example          settings template; copy to .env (git-ignored) and set your paths
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
    ├── render/           server.py + shorts_build.py (the Shorts engine)
    └── <tool>/           server.py + requirements.txt (pinned)
```

## Setup

Needs Windows with Docker Desktop (WSL 2 backend) and an NVIDIA GPU with a current driver
(CUDA 12.8; RTX 50 series supported). Everything else is inside the images.

1. Copy `.env.example` to `.env` and set your own paths (`.env` stays out of git).
2. Build and start (the first build downloads ~10 GB, mostly torch + CUDA):
   ```bash
   docker compose up -d --build
   ```
3. Open http://localhost:8765 and register the MCP server with Claude Code (below).
4. Stable Audio Open is gated on Hugging Face: accept its license on the model page and run
   `hf auth login` once on the host; the token saved under `HOST_HOME\.cache\huggingface` is used.

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

Rebuild and restart one tool after changing it:

```bash
docker compose up -d --build whisperx
```

Stop everything (the containers start again with Docker Desktop otherwise):

```bash
docker compose down
```

## Using it from Claude

Registered once, for every project:

```bash
claude mcp add --scope user --transport http media-toolbox http://localhost:8765/mcp
```

Job tools wait for the result; a long job returns its id, and `wait_for_job` picks it up.

## Using it from scripts

```bash
curl -s -X POST localhost:8765/api/jobs -H "Content-Type: application/json" -d "{\"tool\":\"ffprobe\",\"params\":{\"path\":\"C:\\\\Users\\\\you\\\\Videos\\\\clip.mp4\"}}"
```

Then `GET /api/jobs/<id>` (or `POST /api/jobs/<id>/wait`). `GET /api/tools` lists every tool's parameters.

## Adding a tool

1. `workers/<tool>/server.py`: handlers built with `toolbox_worker.create_app` (same contract as the others).
2. A Dockerfile target (or its own Dockerfile) and a service in `docker-compose.yml`.
3. The worker in `WORKERS` (`gateway/app/config.py`), a `Tool` spec in `gateway/app/tools.py`, and an
   MCP function in `gateway/app/mcp_tools.py`. The dashboard picks the form up automatically.

## Licenses of the models

- WhisperX / faster-whisper large-v3: MIT / Apache 2.0.
- Stable Audio Open 1.0: Stability AI Community License (free under $1M yearly revenue).
- Kokoro-82M: Apache 2.0. (ChatTTS, also in the Hugging Face cache, was not used: its weights are
  non-commercial only.)
