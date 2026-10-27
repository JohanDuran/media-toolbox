# Plan: depth + cutout and FLUX in the media toolbox

Status: **Phase 1 merged** (PR #3, 2026-10-26). **Phase 2 built and tested** on branch `flux-image` (2026-10-26). Phase 3 not started; Phase 4 done for Phase 1's tools (video-studio 0.4.0).

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
- [x] Benchmark (same prompt, seed 42, 1344×768, RTX 5060). "Warm" = second image of the job, models already loaded:

  | Setup | First image | Warm | Quality note |
  |---|---|---|---|
  | GGUF, 20 steps (the old workflow) | 160 s | **126 s** | Reference |
  | GGUF + Turbo LoRA, 8 steps, `--lowvram` | 76 s | **41 s** | Same composition quality, a little less fine texture |
  | GGUF + Turbo, 8 steps, ComfyUI default VRAM | 75 s | **45 s** | Same as above (`--lowvram` is a no-op in ComfyUI 0.42) |
  | GGUF + Turbo, 8 steps, `--highvram` | > 5 min | n/a | Overcommits 8 GB (text encoder 3.7 GB + FLUX 6.6 GB), spills into shared memory: cancelled |
  | Draft 1024×576, Turbo | n/a | **28 s** | Good enough to pick a direction |

  Result: about **3× faster** than the old workflow. The default is ComfyUI's own VRAM management with no flags.
- [x] GPU hand-off: `speak` after a FLUX job logged "unloaded image to free VRAM" (ComfyUI `/free`).
- [x] A prompt queued directly on ComfyUI (as from its web UI) made a toolbox `speak` job wait until it finished
  (submitted 4 s in, started at 63.2 s, ComfyUI done at 63.5 s), then free ComfyUI's models and run.
- [x] 4 drafts finished in 28 s each, against 126 s for one old full-size image.

### What testing changed
- ComfyUI 0.42 has dynamic VRAM management. `--normalvram` no longer exists, and `--lowvram` does nothing, so
  `COMFYUI_ARGS` defaults to empty.
- FLUX is its own compose profile (`flux`), so a toolbox install doesn't download ~11.5 GB unless asked.
- The old standalone stack in `docker\comfyui` is kept as a fallback (stopped). Its MCP on :8189 isn't
  registered anymore; `image_generate` replaces it.

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

### Phase 3 result (2026-10-26): not adopted

Tested in a separate image (trial files on branch `nunchaku-trial`, `trials/nunchaku/`): Nunchaku 1.2.1 wheel
for cu12.8/torch2.8/cp312 (sha256-pinned) + ComfyUI-nunchaku v1.2.1 (without its PuLID face extras), FP4 model
`svdq-fp4_r32-flux.1-dev` (6.6 GB, FLUX.1 dev non-commercial license). The repos now live at github.com/nunchux-ai
(redirected from mit-han-lab and nunchaku-tech). It installed and loaded fine on ComfyUI 0.42.1.

| 1344×768, seed 42/43 | Warm per image | Draft 1024×576 |
|---|---|---|
| GGUF Q4 + Turbo (Phase 2, current) | **45 s** | **28 s** |
| Nunchaku FP4 + Turbo, `cpu_offload: auto` | 83 s | 90 s |
| Nunchaku FP4 + Turbo, `cpu_offload: disable` | 56 s | 58 s |
| Nunchaku FP4, 20 steps, no LoRA | 52 s first, then 192 s (swapping) | n/a |

Nunchaku was **slower** than the current setup in every run, far from the ≥ 2× bar. Part of that is memory pressure
(below), but GGUF is also smaller on the GPU. Decision: drop it, keep GGUF + Turbo.

### Found during Phase 3: the Docker VM runs out of system RAM
- Docker Desktop's WSL VM gets half of the PC's RAM by default (no `.wslconfig`): ~15.8 GB of 31.6 GB.
- ComfyUI peaks at **8.5–10.5 GB of system RAM** while loading FLUX (7.5 GB resident afterwards, models cached).
  The idle GPU workers hold another ~4 GB (WhisperX 1.6, Stable Audio 1.3, TTS 0.9), and the VM's 4 GB swap filled
  up and stayed full.
- Result: the kernel OOM-killed ComfyUI during model loads (trial image and main image alike), and finally WSL
  stopped responding (`Wsl/Service/0x8007274c`), needing a Docker Desktop restart.
- The Phase 2 benchmark ran fine only because memory happened to be freer then. This affects the merged setup.
- **Fix chosen (2026-10-26): options 1 and 2.** `.wslconfig` written with `memory=22GB`, `swap=8GB`, and GPU workers
  recycle after an unload (branch `worker-recycle`). Option 3 not adopted.
- **Result (2026-10-27, VM now 22 GB + 8 GB swap):**
  - speak → FLUX draft → speak → depth → 2 FLUX finals: VM peak **15.1 GB of 22**, swap 0.2 GB, no crash.
  - Workers after their restart: tts 1.9 → 0.6 GB, vision 1.0 → 0.4 GB.
  - With more RAM, ComfyUI kept FLUX in RAM after `/free` (13.4 GB idle), so ComfyUI is recycled too: the image worker
    calls `POST /toolbox/exit` (a tiny route built into the comfyui image) after `/free`. Measured: ComfyUI 10.6 →
    0.7 GB and the VM 11.9 → 4.6 GB after a `speak`. The next FLUX job waited for ComfyUI and ran normally (first
    draft ~80 s, same as before the change).
  - Getting there: stopping Docker Desktop by force left a stale `%LOCALAPPDATA%\Docker\run\dockerInference`
    socket that blocked startup ("starting services: initializing Inference manager"). Moving the `run` folder
    aside (`run.stale-20261027`) fixed it; Docker recreated it.
- Fix options considered:
  1. Give the WSL VM more RAM: `%USERPROFILE%\.wslconfig` with `[wsl2]` `memory=22GB` (applies after `wsl --shutdown`).
  2. Make `/unload` also free system RAM in the GPU workers (they keep ~1–1.6 GB each after unloading VRAM), or
     stop idle workers.
  3. Start ComfyUI with `--disable-pinned-memory` (still peaked at 8.5 GB in the one run before the VM hung;
     not conclusive) or `--cache-none`.
- Measured after a Docker restart (clean VM, swap empty), one sequence: speak → FLUX draft → speak → depth:

  | Container | Start | Peak | End |
  |---|---|---|---|
  | Whole VM | 3.6 GB | **14.5 GB** of 15.8 | 8.3 GB (+2 GB swap) |
  | comfyui | 0.7 GB | **10.3 GB** (loading FLUX) | 3.4 GB after `/free` |
  | tts | 0.6 GB | 2.5 GB | 2.5 GB, even after unload |
  | vision | 0.4 GB | 1.0 GB | 1.0 GB after unload |

  So even a clean start gets within ~1.3 GB of the limit on one draft. The workers' RAM doesn't come back after
  `/unload` (only VRAM does). With `--disable-pinned-memory`, ComfyUI's peak was 9.2 GB instead of 10.3 GB.
- Also seen: the gateway's `POST /api/workers/{name}/unload` timed out at 60 s once while a worker was freeing
  memory (the unload still completed).

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
