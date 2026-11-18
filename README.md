# Media toolbox

> **Part of [video-studio](https://github.com/JohanDuran/video-studio).** Three repos work together: the video-studio
> plugin (the place to start), the [media-toolbox](https://github.com/JohanDuran/media-toolbox) and
> [MangoAI](https://github.com/JohanDuran/MangoAI). To use them, install the plugin and run `/studio-setup`; Claude
> sets up the rest. Changes here can affect the other two: see [docs/ECOSYSTEM.md](docs/ECOSYSTEM.md).

Free, local media tools in Docker, behind one gateway that you open in the browser and Claude calls
as an MCP server, from any project.

| Tool | Worker | Runs on | What it does |
|---|---|---|---|
| `transcribe` | whisperx | GPU | WhisperX transcripts with word timings (.json, .srt, .words.tsv) |
| `timeline_transcript` | gateway | CPU | Maps per-file transcripts onto a Resolve timeline |
| `sfx_generate` + sound library | stable-audio | GPU | Stable Audio Open 1.0 SFX / music beds, cataloged in the shared library |
| `speak` | tts | GPU | Kokoro-82M text to speech (English, Spanish, French, Italian, Portuguese, Hindi). The default voice |
| `speak_clone` | voice-clone | GPU | Chatterbox Turbo: English speech in a cloned voice from a voice profile (a 10–20 s clip). See [Cloned voices](#cloned-voices) |
| `ffmpeg`, `ffprobe` | render | CPU | Any ffmpeg command, media info |
| `image_generate` | image → comfyui | GPU | FLUX.1 [dev] images (GGUF Q4 + Turbo LoRA, 8 steps) through a pinned ComfyUI. Optional `flux` profile. See [FLUX images](#flux-images) |
| `short_info`, `short_frames` | render | CPU | Helpers for `short.json` Shorts: read a spec, grab labelled frame grids from a recording. See [docs/short-spec.md](docs/short-spec.md) |
| `short_build`, `short_preview` | render | CPU | **Deprecated**: [MangoAI](https://github.com/JohanDuran/MangoAI) now previews, edits and renders `short.json` projects (same look, −14 LUFS). Kept working for old projects |

Dashboard: **http://localhost:8765**. MCP: **http://localhost:8765/mcp**.

**The toolbox makes media; [MangoAI](https://github.com/JohanDuran/MangoAI) edits and renders it.** Since v0.4.0
HyperFrames renders (`render_hyperframes`), the parallax shots and their depth/cutout models are gone from here:
MangoAI's `comp_render` and `export` render HyperFrames, and a plain zoom or an animation replaces parallax.
MangoAI also calls this toolbox from its Toolbox tab (sounds, voice lines, transcription).

## Architecture

```
 browser ──► http://localhost:8765 ◄── Claude (MCP, any project)
                     │
              ┌──────┴──────┐  gateway: dashboard (React) · REST /api · MCP /mcp
              │   gateway   │  job queue (SQLite) · GPU lock · Windows path conversion
              └──┬───┬───┬──┴───┐
  whisperx  stable-audio  tts  voice-clone  image  render   one image per tool, internal network only
                                     │
                                  comfyui   (flux profile; web UI at 127.0.0.1:8188)
                     │
   HOST_HOME (e.g. C:\Users\you) → /host   (inputs and outputs, normal Windows paths in and out)
   HF / torch caches under HOST_HOME\.cache   (models downloaded once, shared)
```

- **One job per worker, one GPU job at a time.** Before a GPU job starts, the other GPU workers are
  asked to unload their models, so an 8 GB card never runs out of VRAM. A model stays loaded between
  jobs of the same tool, so the second transcription starts instantly. After an unload, the worker restarts
  itself (a few seconds) so its system RAM is returned as well (`TOOLBOX_RECYCLE_ON_UNLOAD`, on by default).
  ComfyUI is restarted the same way (`COMFYUI_RECYCLE_ON_UNLOAD`): its `/free` keeps FLUX in system RAM (13+ GB).
- **System RAM:** Docker Desktop's VM gets half of the PC's RAM by default. With FLUX on, give it more: loading
  FLUX peaks at ~10 GB in ComfyUI. On a 32 GB PC, `%USERPROFILE%\.wslconfig` with `[wsl2]`, `memory=22GB` and
  `swap=8GB`, then `wsl --shutdown` and start Docker Desktop again.
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
├── get.ps1               clone-free install of one release (what /studio-setup runs)
├── docker-compose.yml    all services
├── docs/short-spec.md    the short.json format for vertical Shorts
├── gateway/
│   ├── app/              FastAPI: api, MCP tools (mcp_tools.py), queue (runner.py), tool specs (tools.py), shorts.py
│   └── web/              dashboard (React + TypeScript, built into the gateway image)
└── workers/
    ├── Dockerfile.gpu    one shared torch base + a target per GPU tool
    ├── Dockerfile.render ffmpeg + the Shorts helpers (no Node or Chrome)
    ├── common/           the worker contract (toolbox_worker.py), the Shorts spec parser (shorts_spec.py),
    │                     the pinned torch build
    ├── render/           server.py + shorts_build.py (the deprecated Shorts engine)
    └── <tool>/           server.py + requirements.txt (pinned)
```

## Requirements

- Windows 10/11 with [Docker Desktop](https://www.docker.com/products/docker-desktop/) (WSL 2 backend).
- For the GPU tools (transcription, sound effects, text to speech): an NVIDIA GPU with a current
  driver (CUDA 12.8; RTX 50 series supported). 8 GB of VRAM is enough. Without one, ffmpeg
  and the Shorts helpers still work.
- About 40 GB of disk for the images and models.

## Quick install

**With Claude (recommended):** install the video-studio plugin (below) and run `/studio-setup`. Claude installs the
release the plugin is tested with, no clone needed.

**By hand, no clone:** one release, into `%USERPROFILE%\.video-studio\media-toolbox`:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/JohanDuran/media-toolbox/v0.5.0/get.ps1))) -Version v0.5.0
```

**From a clone** (development, or to build the images yourself):

```bash
git clone https://github.com/JohanDuran/media-toolbox
```

```bash
powershell -ExecutionPolicy Bypass -File media-toolbox\install.ps1
```

`get.ps1` downloads `install.ps1`, `docker-compose.yml`, `.env.example` and `docs/short-spec.md` for that tag and runs
`install.ps1 -Version <tag>`; pass it the same options. Upgrade by running it again with a newer `-Version`.
`install.ps1` checks Docker and your GPU, creates your studio folder (default
`%USERPROFILE%\media-studio`, change it with `-StudioDir`), writes `.env`, pulls the prebuilt images from
`ghcr.io` and starts everything. Options: `-RecordingsDir` (your raw recordings, kept read-only; default `media-studio\recordings`),
`-NoGpu`, `-Version vX.Y.Z` (pin the image release), `-Build` (build the images yourself; clone only), `-Force`
(rewrite `.env`). It's safe to run again. The running release shows as `version` in `/api/state` and `toolbox_status`.

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

Run these in the toolbox folder (the clone, or `%USERPROFILE%\.video-studio\media-toolbox` for the clone-free install).

```bash
docker compose up -d
```

```bash
docker compose ps
```

```bash
docker compose logs -f gateway
```

Update the images for the release `TOOLBOX_VERSION` in `.env` names (to change release, run `get.ps1` or
`install.ps1 -Version` with the new tag; with the plugin, `/studio-setup update`):

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
protected folders, `COMPOSE_PROFILES` (`gpu`, `gpu,flux` or empty), `TOOLBOX_VERSION` (image tag) and
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

## Cloned voices

`speak_clone` reads text in a real person's voice with Chatterbox Turbo (Resemble AI, 350M). Kokoro (`speak`) stays
the default; use a cloned voice when a video should sound like you. **Only clone a voice with that person's consent.**

1. **Make a voice profile:** put a `<name>.wav` in `VOICES_DIR` (default `<OUTPUTS_DIR>\voices`). Use 10–20 s of clean
   speech by one person: a good mic, no music or room echo, normal narration pace. Only the first ~15 s are used, and
   it must be longer than 5 s. Loudness-normalize it (e.g. `ffmpeg -i in.wav -af loudnorm=I=-18:TP=-1.5 -ac 1 out.wav`).
   The clone copies the clip's delivery too, so record it the way you want the voiceover to sound.
2. **Speak:** `speak_clone` with `voice: <name>`, or `ref_path` for a one-off clip. Leave `voice` out to list the profiles.
3. **Numbers are spelled out first** (`$43,300` → "forty-three thousand, three hundred dollars", `7.32%` → "seven point
   three two percent", `2026` → "twenty twenty-six"): voice-clone models misread digits. Turn it off with
   `spell_numbers: false`, or write a number as words yourself to control how it's read.
4. Long text is split at sentences (~280 characters per pass) and blank lines become `pause` seconds of silence.
   Reuse a `seed` for a repeatable take.

- **Speed and VRAM on an RTX 5060 (8 GB):** ~4 GB of VRAM. A cold start takes ~25 s (model + reference), then about
  1 s per 3 s of speech. The encoded reference is kept while the same profile is used again.
- **English only.** For Spanish and other languages, use Kokoro.
- **Watermark:** every output carries Resemble's inaudible Perth watermark, which marks it as AI-generated. It
  survives MP3 and edits. On YouTube, tick "altered or synthetic content" for videos with a realistic cloned voice.
- **Why Chatterbox Turbo:** in a blind test on an 8 GB RTX 5060 against Chatterbox (original) and Fish Audio S2 Pro
  (q6_k GGUF via s2.cpp), it was the only one word-perfect on both the raw and the spelled-out script, the fastest
  (~real time), and the lightest. S2 Pro needed ~6.9 GB, ran ~2.6× slower than real time and glitched on one take.
- **Pinned:** Chatterbox is retired upstream (August 2026, MIT, no updates). Its requirements are pinned, and
  `workers/voice-clone/overrides.txt` keeps the base torch 2.8 build, because chatterbox pins torch 2.6, which has no
  RTX 50 kernels.

## FLUX images

`image_generate` makes images with FLUX.1 [dev] (GGUF Q4) in a pinned ComfyUI, under the same GPU lock as every
other tool. It's optional: turn it on with `COMPOSE_PROFILES=gpu,flux` in `.env`, then `docker compose up -d`. The
first start downloads the models (~11.5 GB) into the `comfyui_models` volume. An existing volume of that name is
reused.

- **Speed on an RTX 5060 (8 GB):** about 45 s per 1344×768 final and 28 s per 1024×576 draft, using the Turbo LoRA
  at 8 steps. Without Turbo, at 20 steps: about 125 s. The first image after another GPU tool ran takes ~30 s longer
  (model load).
- **Drafts, then finals:** generate a few `draft`s, pick one, and re-run its seed with `quality: final`. Each PNG
  has a `.json` next to it with the prompt, seed, size and steps.
- **After another GPU tool runs**, ComfyUI is restarted to return its RAM, so the next image waits ~20 s for it
  and loads FLUX again (~80 s for that first draft).
- **ComfyUI's own UI** stays at http://127.0.0.1:8188 for your experiments. While it works on something, toolbox GPU
  jobs wait. When a toolbox GPU job needs the card, ComfyUI's models are freed first. Jobs run from its UI don't free
  the toolbox's models first, so start them when the toolbox is idle.
- **VRAM mode:** leave `COMFYUI_ARGS` unset. ComfyUI's own dynamic VRAM management is fastest on 8 GB. `--highvram`
  overcommits the card and is several times slower.
- **License:** the FLUX.1 [dev] weights and the Turbo LoRA are non-commercial. Under the FLUX.1 [dev] license, the
  images you generate can be used commercially.

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
- Chatterbox Turbo (Resemble AI): MIT. Outputs are watermarked (Perth).
- Depth Anything V2 **Small**: Apache 2.0. (The Base and Large versions are non-commercial: don't swap them in.)
- BiRefNet: MIT.
- FLUX.1 [dev] (city96 GGUF) and FLUX.1-Turbo-Alpha LoRA: FLUX.1 [dev] Non-Commercial License (weights);
  generated images may be used commercially.
