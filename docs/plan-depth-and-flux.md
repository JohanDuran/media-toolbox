# Plan: depth + cutout and FLUX in the media toolbox

Status: **Phase 1 built and tested** on branch `vision-parallax` (2026-10-26, not committed yet). Phases 2–4 not started.

Goal: two new capabilities inside the toolbox, under its one job queue and GPU lock:

1. **Depth + cutout** turns any still (listing photos, screenshots, generated images) into a moving
   2.5D parallax shot, and gives clean cutouts for thumbnails and "text behind the subject".
2. **FLUX image generation** moves the existing ComfyUI setup into the toolbox, made faster, so images are
   queued with transcription, SFX and TTS instead of fighting them for the 8 GB card.

## Constraints

- Free and local by default. No cloud calls.
- RTX 5060, 8 GB VRAM: one GPU job at a time (the existing GPU lock in `gateway/app/runner.py`).
- Every model's weights must be OK for a monetized channel, or (for FLUX.1 dev) its *outputs* must be.
- Pinned versions, like the rest of the toolbox: no `git clone` of `main`, no unpinned `pip install`.
- Same worker contract as today (`workers/common/toolbox_worker.py`): `/health`, `/info`, `/run`, `/jobs/{id}`,
  `/jobs/{id}/cancel`, `/unload`, `/gpu`.

## Where things stand today

| | Today |
|---|---|
| Toolbox GPU workers | whisperx, stable-audio, tts; share torch 2.8.0 + CUDA 12.8 base layers |
| GPU lock | `Runner._schedule` runs one GPU job at a time; `_make_vram_room` calls `/unload` on the others first |
| ComfyUI | Separate stack in `Documents\Projects\docker\comfyui`: ComfyUI + ComfyUI-GGUF, its own MCP on :8189 |
| FLUX setup | `flux1-dev-Q4_K_S.gguf`, T5 Q5_K_M GGUF, 20 steps, 1024², `--lowvram`; ~60–100 s per image |
| FLUX models | Docker volume `comfyui_models` (~10.8 GB, external) |
| ComfyUI versions | Unpinned: ComfyUI and ComfyUI-GGUF cloned from `main`, torch unpinned |
| GPU sharing | ComfyUI doesn't know about the toolbox's lock, and neither knows about LibreChat's Ollama |

## Target architecture

```
                         gateway (queue + GPU lock)
        ┌──────────┬────────────┬───────┬──────────┬────────────┐
     whisperx  stable-audio    tts    vision      image          render (CPU)
      (GPU)       (GPU)       (GPU)    (GPU)   (adapter, no GPU)   HyperFrames + ffmpeg
                                         new          │  new        + parallax template (new)
                                                      ▼
                                                   comfyui (GPU)   moved in from the old stack
                                                   UI at 127.0.0.1:8188
```

- **vision**: new native worker, built from the shared GPU base image. Depth Anything V2 Small + BiRefNet.
- **image**: small adapter that speaks the toolbox worker contract and drives ComfyUI's HTTP API.
  The gateway treats it as a GPU worker, so the lock covers ComfyUI.
- **comfyui**: ComfyUI itself, pinned, sharing the toolbox's torch base, reusing the `comfyui_models` volume.

---

## Phase 1: depth + cutout (vision worker + parallax)

### 1a. `vision` worker
- **New target** `vision` in `workers/Dockerfile.gpu` (`FROM base`), with `workers/vision/server.py` and a pinned
  `requirements.txt` (transformers, timm, einops, kornia, opencv-python-headless, pillow).
- **Models**, downloaded once into the shared HF cache, revision-pinned:

  | Use | Model | License | Size | VRAM |
  |---|---|---|---|---|
  | Depth | `depth-anything/Depth-Anything-V2-Small-hf` | Apache 2.0 | ~100 MB | < 1 GB |
  | Cutout | `ZhengPeng7/BiRefNet` (remote code, pin the revision) | MIT | ~900 MB | ~2–4 GB at 1024 px |
  | Fill behind subject | OpenCV inpaint (Telea); LaMa only if it isn't good enough | n/a / Apache 2.0 | 0 / ~200 MB | 0 / ~1–2 GB |

  **Only the Small depth model.** Depth Anything V2 Base and Large are non-commercial.
- **Loading:** lazy-load each model on first use, keep it loaded between jobs, and free both on `/unload`.
- **Actions:**
  - `depth`: image(s) → 16-bit grayscale depth PNG (near = white), same size as the input.
  - `cutout`: image(s) → RGBA PNG + mask PNG. Optional `refine` (edge feather px).
  - `plate`: image + mask → background with the subject removed and the hole filled (for layered parallax).
  - All accept a list of images, so a whole Short's stills run as one job with one model load.

### 1b. Gateway
- **`config.py`:** `WORKERS["vision"] = {"url": "http://vision:8000", "gpu": True, "title": "Depth + cutout"}`.
- **`tools.py`:** three tools, each with defaults and write protection like the others:
  - `image_depth(images, out_dir?)`
  - `image_cutout(images, out_dir?, refine?)`
  - `image_plate(image, mask, out_dir?)`
  - **Outputs:** default to a `stills/` folder next to the first input, with suffixes `_depth.png`, `_cut.png`,
    `_mask.png`, `_plate.png`. Inputs in protected folders (`recordings`) are fine to read, and outputs never go there.
- **`docker-compose.yml`:** a `vision` service in the `gpu` profile, same mounts and env as the other GPU workers.
- **MCP and dashboard:** they pick the tools up from `TOOLS` automatically. Add a before/after image preview to the
  job result on the dashboard.

### 1c. Parallax render
- **Template:** a HyperFrames parallax template in `workers/render/templates/parallax/` with two modes:
  - `layers` (default, safest): background plate + cutout on separate planes, moved at different speeds.
  - `depth`: one three.js plane displaced by the depth map (stronger 3D, can smear at edges).
- **Camera path:** a pure function of time (push-in, pull-out, pan left or right, orbit), so frame-exact
  rendering works.
- **New render action and tool** `parallax(image, depth?, cutout?, plate?, move, duration, size, out)`:
  it copies the template, writes the parameters, and renders. If depth or cutout files are missing, it fails with
  "run image_depth / image_cutout first" (the queue has no job chaining, so Claude chains the two calls).
- **Sizes:** `1080x1920` (Shorts), `1920x1080`, or the source size. `mov` keeps alpha when wanted.

### 1d. Shorts integration (optional, can slip to later)
- **Layer option** in `short.json`: `"parallax": {"move": "push", "strength": 0.6}` on a still layer, so a
  Short's stills move without a separate render step.
- **Implementation:** `workers/render/shorts_build.py` renders those layers through the parallax template.
  Document it in `docs/short-spec.md`.

### Phase 1 done when
- [x] Depth + cutout for the Quincy stills run as one job each. Measured on the RTX 5060:
  - `image_depth`, 16 stills: **4.4 s** including the model load. The first run ever, which also downloaded the model: 21 s.
  - `image_cutout` with plate, 11 stills: 71 s on the first run, about 52 s of it downloading and loading
    BiRefNet (~900 MB); then about 2 s per still.
  - VRAM used by the vision worker with both models loaded: **~2.5 GB** (3.9 GB for the whole card).
- [x] 5 s parallax clips render cleanly at 1080×1920 and 1920×1080: **~26 s per 5 s clip** (software GPU in
  the render worker's Chrome).
- [x] The vision worker shows in `toolbox_status`. GPU hand-off checked: `speak` unloaded vision first, then
  `image_depth` unloaded tts.
- [x] README tool table and a "Stills to motion" section.

### What testing changed
- **Depth is the default mode, not layers.** On a real listing photo, layers looked wrong. BiRefNet kept the house but
  not its front bushes, and pulled in loose leaf fragments, so the house slid in front of its own bushes. The
  filled-in plate is also very smeary under a large subject. Depth mode looked clean, so it is now the default, and layers is
  documented as being for one clean subject only (a person, a product).
- **Depth maps are softened by default** (`soften` 0.006 of the long side: dilate near objects, then blur). Without it,
  a pan tears a stretched line along hard outlines, such as a house wall against the sky. With it, the background bends
  smoothly. `soften: 0` gives the raw map.
- **Screenshots need cropping first.** Cutouts of Zillow screenshots include UI buttons. Crop to the photo before
  running the vision tools.
- **1d (`parallax` inside `short.json`) is not built.** For now, render the clip with `parallax` and use it as a shot
  source.

---

## Phase 2: FLUX in the toolbox, under the GPU lock, with a Turbo LoRA

### 2a. ComfyUI moves into the toolbox
- **New target** `comfyui` in `workers/Dockerfile.gpu`, `FROM base`. It reuses torch 2.8.0 + cu128 instead of a
  second ~6 GB torch install.
- **Pinned:** ComfyUI at a release tag, ComfyUI-GGUF at a commit. Requirements installed with the torch
  constraints file so torch never gets replaced.
- **Compose:** a `comfyui` service in the `gpu` profile, ports `127.0.0.1:8188:8188` (UI stays available,
  local only).
- **Models:** mounts the existing external volume `comfyui_models` at `/opt/ComfyUI/models`, so nothing is
  downloaded again.
- **Output folder:** a toolbox cache path. The adapter copies finished images to the requested folder.
- **Memory flag:** start with `--lowvram` as today, then benchmark `--normalvram`. With the toolbox unloading the
  other workers first, FLUX may now fit without offloading, which would be faster.

### 2b. `image` adapter worker
- **What it is:** a small FastAPI app built on `toolbox_worker.create_app` (CPU-only image, no torch), at
  `workers/image/server.py`. It holds the workflow templates.
- **`/run`, action `generate`:**
  - fills the workflow (prompt, size, seed, steps, LoRA on/off) and `POST /prompt` to ComfyUI;
  - polls `/history/{id}` and reports progress;
  - copies outputs to `out_dir` and writes a sidecar `.json` per image (prompt, seed, size, steps, workflow
    name), so any image can be reproduced.
- **`/unload`:** `POST /free {"unload_models": true, "free_memory": true}` on ComfyUI.
- **`/health`:**
  - `busy` when ComfyUI's `/queue` has running or pending items, including jobs started from its own UI;
  - `model_loaded` is tracked from the last job and cleared on unload.
- **Cancel:** `POST /interrupt` on ComfyUI.
- **Port:** the old `flux_workflow.json` and the `_snap` / preview logic from `docker/comfyui/mcp/server.py`.

### 2c. Gateway changes
- **`config.py`:** `WORKERS["image"] = {..., "gpu": True}`.
- **`runner.py` `_schedule`:** also count the GPU as busy when any GPU worker's health reports `busy` without a
  toolbox job. That is ComfyUI being used from its own UI, and toolbox GPU jobs wait for it to finish.
- **Known limit:** jobs run from the ComfyUI UI don't unload the toolbox's models first. With `--lowvram` that
  makes FLUX slower, not crash. Write this in the README.
- **`tools.py`:** `image_generate`:

  | Param | Default | Notes |
  |---|---|---|
  | `prompt` | required | Text to render goes in quotes |
  | `size` | `16:9` | Presets `16:9` (1344×768), `9:16` (768×1344), `1:1` (1024²), `thumbnail` (1280×720 via 1344×768 + crop), or `WxH` |
  | `count` | 1 | Up to 8, one seed each, one job |
  | `quality` | `draft` | `draft` = 768-class size, Turbo 8 steps; `final` = full size, Turbo 8–12 steps |
  | `seed` | random | Reuse a draft's seed to re-render it as final |
  | `out_dir` | `<OUTPUTS_DIR>/images` | Usually the project's `images/` folder |

### 2d. Turbo LoRA
- **Add-on:** `alimama-creative/FLUX.1-Turbo-Alpha` (~700 MB) in `comfyui_models/loras`, via a one-shot download
  service like the old `comfyui-models` job.
- **Workflows:**
  - `flux_turbo.json`: GGUF UNet → `LoraLoaderModelOnly` (turbo) → 8 steps.
  - `flux_base.json`: the current 20-step workflow, kept for comparison and as a fallback.
- **Licensing:** the LoRA is a FLUX.1 dev derivative, so the same rule applies. The weights are
  non-commercial, but the images are yours to use.

### 2e. Retire the old stack
- **Order:** only after phase 2 passes. Stop `docker/comfyui` (`comfyui`, `comfyui-mcp`) and remove the
  `comfyui` MCP from Claude's config if it's registered.
- **Keep:** the folder for a while, and **never delete the `comfyui_models` volume**. The toolbox uses it.

### Phase 2 done when
- [ ] Benchmark table filled (same prompt and seed, 1344×768):

  | Setup | s / image | VRAM peak | Quality note |
  |---|---|---|---|
  | Old: GGUF, 20 steps, lowvram | 60–100 (today) | | |
  | GGUF + Turbo, 8 steps, lowvram | | | |
  | GGUF + Turbo, 8 steps, normalvram | | | |
- [ ] An `image_generate` job queued behind a `transcribe` job waits, then unloads WhisperX, then runs.
- [ ] A ComfyUI UI job in progress makes a toolbox `speak` job wait.
- [ ] 4 drafts at 768-class size finish noticeably faster than one old 1024² image.

---

## Phase 3: Nunchaku trial (separate, allowed to fail)

- **What:** Nunchaku (SVDQuant 4-bit) has a ComfyUI node and an FP4 FLUX.1 dev model for RTX 50-series cards.
  It's reported to be several times faster than GGUF.
- **Isolation:** an `engine` setting on the image worker (`gguf` default, `nunchaku` opt-in), with a separate
  workflow `flux_nunchaku.json`.
- **Risk:** the Nunchaku wheel must match torch 2.8.0, CUDA 12.8, Python 3.12 and sm_120 exactly. If no
  matching wheel exists, stop and record why. Don't change the shared torch base for it.
- **Adopt only if:** ≥ 2× faster than phase 2's best and quality holds on thumbnails and photo-real stills.
  Otherwise remove it.
- **License:** check the Nunchaku code and the quantized model's license when installing.

---

## Phase 4: plugin, dashboard, docs

- **video-studio plugin** (`Documents\Projects\video-studio`):
  - `video-toolkit`: new tools, when to use them, timings, the "Small depth model only" rule.
  - `plan`: stills → parallax and FLUX images as options per beat.
  - `short`: use parallax on stills by default for realty Shorts.
  - `editing-rules`: FLUX outputs OK for the channel; images that show real listings must not be faked with FLUX
    (every fact on screen comes from `listing_facts.md`).
- **Toolbox README:** tool table, architecture diagram, VRAM notes, the ComfyUI UI caveat.
- **Dashboard:** image thumbnails in job results; an Images page listing recent generations with prompt + seed.
- **`install.ps1`:** create or reuse the `comfyui_models` volume and download the FLUX files + Turbo LoRA when the
  gpu profile is on.

## Budget (rough, to be measured)

| | Disk | VRAM peak | Time |
|---|---|---|---|
| vision worker | +1–2 GB (shares torch) | ~2–4 GB | ~1 s per still |
| comfyui in toolbox | +2–3 GB image; models already on disk | most of 8 GB | goal: < 30 s per final image |
| Turbo LoRA | +0.7 GB | small | n/a |
| Nunchaku (trial) | +~7 GB model | lower than GGUF | to be measured |

## Licenses at a glance

| Model | Weights | Outputs usable on the channel |
|---|---|---|
| Depth Anything V2 **Small** | Apache 2.0 | Yes |
| Depth Anything V2 Base / Large | Non-commercial | **Don't use** |
| BiRefNet | MIT | Yes |
| LaMa (if needed) | Apache 2.0 | Yes |
| FLUX.1 dev (GGUF, Turbo LoRA, Nunchaku quant) | Non-commercial | Yes, per the FLUX.1 dev license (outputs) |
| RMBG-2.0 (alternative cutout) | Non-commercial | **Don't use** |

Re-check each license at install time. Terms change.

## Open questions

1. Default output folder for `image_generate` and the vision tools: project `images/` and `stills/`, or
   `OUTPUTS_DIR`?
2. Should the parallax layer in `short.json` (1d) be part of phase 1, or wait?
3. Remove the old `docker/comfyui` folder after phase 2, or keep it as a fallback?
4. LibreChat's Ollama also shares the GPU. Leave it outside the lock, or stop it when the toolbox runs?
