"""Vision worker: depth maps, cutouts and background plates for stills (local GPU).

Actions (each takes a list of images, so a whole Short's stills share one model load):
  depth    images, out_dir, soften -> <stem>_depth.png (8-bit grayscale, near = white, same size as the image);
           soften (fraction of the long side, default 0.006) rounds off depth edges for cleaner parallax
  cutout   images, out_dir, refine, plate
           -> <stem>_cut.png (RGBA) + <stem>_mask.png; with plate, also <stem>_plate.png:
              the background with the subject removed and the hole filled (for layered parallax)

Models (weights OK for commercial use; revisions pinned):
  depth   Depth Anything V2 Small (Apache 2.0). Base and Large are non-commercial: never switch to them.
  cutout  BiRefNet (MIT)
"""

import os
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

from toolbox_worker import JobContext, create_app, cuda_info, free_cuda

# GPU workers run as root and the render worker as "node": keep outputs writable for both.
os.umask(0)

DEPTH_MODEL, DEPTH_REV = "depth-anything/Depth-Anything-V2-Small-hf", "5426e4f0f36572d16453bbda7a8389317b1bef99"
CUT_MODEL, CUT_REV = "ZhengPeng7/BiRefNet", "e2bf8e4460fc8fa32bba5ea4d94b3233d367b0e4"
CUT_SIZE = 1024  # BiRefNet's training resolution
IMAGENET_MEAN = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
IMAGENET_STD = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}

_models: dict = {}


def _require_gpu() -> None:
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU not available; refusing to run on CPU")


def depth_model(ctx: JobContext):
    if "depth" not in _models:
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation

        ctx.log(f"loading {DEPTH_MODEL}")
        proc = AutoImageProcessor.from_pretrained(DEPTH_MODEL, revision=DEPTH_REV)
        model = AutoModelForDepthEstimation.from_pretrained(DEPTH_MODEL, revision=DEPTH_REV).to("cuda").eval()
        _models["depth"] = (proc, model)
    return _models["depth"]


def cut_model(ctx: JobContext):
    if "cut" not in _models:
        from transformers import AutoModelForImageSegmentation

        ctx.log(f"loading {CUT_MODEL}")
        torch.set_float32_matmul_precision("high")
        model = AutoModelForImageSegmentation.from_pretrained(CUT_MODEL, revision=CUT_REV, trust_remote_code=True)
        _models["cut"] = model.to("cuda").eval().half()
    return _models["cut"]


def loaded() -> bool:
    return bool(_models)


def unload() -> None:
    _models.clear()
    free_cuda()


def _images(p: dict) -> list[Path]:
    out: list[Path] = []
    for raw in p["images"]:
        path = Path(raw)
        if path.is_dir():
            out += sorted(f for f in path.iterdir() if f.suffix.lower() in IMAGE_EXTS
                          and not f.stem.endswith(("_depth", "_cut", "_mask", "_plate")))
        elif path.exists():
            out.append(path)
        else:
            raise FileNotFoundError(f"no such image: {path}")
    if not out:
        raise ValueError("no images found")
    return out


def _out_dir(p: dict, first: Path) -> Path:
    out = Path(p["out_dir"]) if p.get("out_dir") else first.parent / "stills"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _rgb(path: Path) -> Image.Image:
    img = Image.open(path)
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, (0, 0, 0))
        bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
        return bg
    return img.convert("RGB")


@torch.inference_mode()
def depth(p: dict, ctx: JobContext) -> dict:
    _require_gpu()
    images = _images(p)
    out = _out_dir(p, images[0])
    proc, model = depth_model(ctx)
    soften = float(p["soften"]) if p.get("soften") is not None else 0.006
    files = []
    for i, path in enumerate(images):
        ctx.check_cancel()
        img = _rgb(path)
        inputs = {k: v.to("cuda") for k, v in proc(images=img, return_tensors="pt").items()}
        pred = model(**inputs).predicted_depth  # relative inverse depth: bigger = nearer
        pred = F.interpolate(pred.unsqueeze(1), size=(img.height, img.width), mode="bicubic",
                             align_corners=False)[0, 0].float().cpu().numpy()
        lo, hi = np.percentile(pred, [1, 99])  # robust range: a few outlier pixels don't flatten the map
        d = np.clip((pred - lo) / max(hi - lo, 1e-6), 0, 1).astype(np.float32)
        if soften > 0:
            # Grow near objects slightly, then blur: parallax then bends the background next to an edge
            # instead of tearing a stretched line along the object's outline.
            px = max(1, round(max(img.size) * soften))
            d = cv2.dilate(d, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * px + 1, 2 * px + 1)))
            d = cv2.GaussianBlur(d, (0, 0), px * 1.5)
        dst = out / f"{path.stem}_depth.png"
        Image.fromarray((d * 255).round().astype(np.uint8), "L").save(dst)
        files.append({"image": str(path), "depth": str(dst), "size": [img.width, img.height]})
        ctx.log(f"{path.name} -> {dst.name}")
        ctx.set_progress((i + 1) / len(images))
    return {"out_dir": str(out), "files": files}


def _plate(img: Image.Image, mask: np.ndarray, grow: int) -> Image.Image:
    """Remove the subject and fill the hole from the surrounding background (OpenCV Telea)."""
    rgb = np.array(img)
    hole = (mask > 24).astype(np.uint8) * 255
    hole = cv2.dilate(hole, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1, 2 * grow + 1)))
    # Inpaint at <= 1024 px (big holes get slow at full size), then use the fill only inside the hole.
    scale = min(1.0, 1024 / max(rgb.shape[:2]))
    small = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else rgb
    small_hole = cv2.resize(hole, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST)
    filled = cv2.inpaint(small, small_hole, max(3, int(12 * scale)), cv2.INPAINT_TELEA)
    if scale < 1:
        filled = cv2.resize(filled, (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_CUBIC)
    soft = cv2.GaussianBlur(hole, (0, 0), max(1.0, grow / 2)).astype(np.float32)[..., None] / 255
    return Image.fromarray((rgb * (1 - soft) + filled * soft).round().astype(np.uint8))


@torch.inference_mode()
def cutout(p: dict, ctx: JobContext) -> dict:
    _require_gpu()
    images = _images(p)
    out = _out_dir(p, images[0])
    refine = float(p.get("refine") or 0)
    want_plate = bool(p.get("plate"))
    model = cut_model(ctx)
    files = []
    for i, path in enumerate(images):
        ctx.check_cancel()
        img = _rgb(path)
        x = torch.from_numpy(np.array(img.resize((CUT_SIZE, CUT_SIZE), Image.BILINEAR))).float() / 255
        x = ((x.permute(2, 0, 1).unsqueeze(0) - IMAGENET_MEAN) / IMAGENET_STD).to("cuda").half()
        pred = model(x)[-1].sigmoid()
        pred = F.interpolate(pred.float(), size=(img.height, img.width), mode="bilinear",
                             align_corners=False)[0, 0].cpu().numpy()
        mask = (pred * 255).round().astype(np.uint8)
        if refine > 0:
            mask = cv2.GaussianBlur(mask, (0, 0), refine)
        cut = img.convert("RGBA")
        cut.putalpha(Image.fromarray(mask, "L"))
        entry = {"image": str(path), "size": [img.width, img.height],
                 "cut": str(out / f"{path.stem}_cut.png"), "mask": str(out / f"{path.stem}_mask.png"),
                 "subject_fraction": round(float((mask > 127).mean()), 3)}
        cut.save(entry["cut"])
        Image.fromarray(mask, "L").save(entry["mask"])
        if want_plate:
            entry["plate"] = str(out / f"{path.stem}_plate.png")
            _plate(img, mask, grow=max(4, round(max(img.size) / 150))).save(entry["plate"])
        files.append(entry)
        ctx.log(f"{path.name} -> {Path(entry['cut']).name} (subject {entry['subject_fraction']:.0%} of the frame)")
        ctx.set_progress((i + 1) / len(images))
    return {"out_dir": str(out), "files": files}


app = create_app("vision", f"depth-anything-v2-small@{DEPTH_REV[:7]} + birefnet@{CUT_REV[:7]}",
                 {"depth": depth, "cutout": cutout}, info=cuda_info, loaded=loaded, unload=unload)
