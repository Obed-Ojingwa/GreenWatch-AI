"""SAM segmentation with caching, mask storage and overlays."""
from __future__ import annotations

import hashlib
import io

import numpy as np
from PIL import Image

from app.core.config import get_settings
from app.core.errors import MetaApiError
from app.services.meta_client import MetaClient
from app.services.storage import Storage

OVERLAY_COLORS = {
    "buildings": (239, 68, 68),
    "roofs": (239, 68, 68),
    "trees": (34, 197, 94),
    "vegetation": (132, 204, 22),
}


class SegResult:
    def __init__(self, mask: np.ndarray, from_cache: bool, instances: int):
        self.mask = mask
        self.from_cache = from_cache
        self.instances = instances
        self.pixel_count = int(np.count_nonzero(mask))
        self.coverage = self.pixel_count / mask.size if mask.size else 0.0


def cache_key(image_png: bytes, prompt: str) -> str:
    sam = get_settings().sam_model
    h = hashlib.sha256(image_png).hexdigest()
    p = hashlib.sha256(f"{prompt}|{sam}".encode()).hexdigest()[:16]
    return f"cache/{h}/{p}.png"


def mask_to_png(mask: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray((mask.astype(np.uint8)) * 255, mode="L").save(buf, format="PNG")
    return buf.getvalue()


def png_to_mask(data: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(data)).convert("L")) > 0


def make_overlay(image_rgb: np.ndarray, mask: np.ndarray, category: str, alpha: float = 0.5) -> bytes:
    color = np.array(OVERLAY_COLORS.get(category, (59, 130, 246)), dtype=np.float32)
    out = image_rgb.astype(np.float32).copy()
    out[mask] = out[mask] * (1 - alpha) + color * alpha
    buf = io.BytesIO()
    Image.fromarray(out.astype(np.uint8)).save(buf, format="PNG")
    return buf.getvalue()


def segment_image(
    meta: MetaClient, storage: Storage, image_png: bytes, image_rgb: np.ndarray, prompt: str
) -> SegResult:
    """Segment one prepared image. Cached by sha256(image) + prompt + SAM model."""
    key = cache_key(image_png, prompt)
    if storage.exists(key):
        mask = png_to_mask(storage.download_bytes(key))
        if mask.shape == image_rgb.shape[:2]:
            return SegResult(mask, True, instances=-1)
    masks = meta.segment(image_png, prompt)
    if not masks:
        mask, n = np.zeros(image_rgb.shape[:2], dtype=bool), 0
    else:
        for m in masks:
            if m.shape != image_rgb.shape[:2]:
                raise MetaApiError(f"SAM mask shape {m.shape} != image shape {image_rgb.shape[:2]}")
        mask, n = np.any(np.stack(masks), axis=0), len(masks)
    storage.upload_bytes(key, mask_to_png(mask), "image/png")
    return SegResult(mask, False, instances=n)
