"""Decode the segmentation-mask encodings a SAM-style API may return into a bool HxW array.

Which encoding Meta's SAM 3.1 endpoint uses is a Gate 0 finding (docs/meta-api-notes.md). These are
the standard encodings; `decode_mask` sniffs among them so the adapter only needs to know *where* in
the response the masks are (META_SEGMENT_MASKS_PATH), not how each one is encoded.

Supported:
  - base64 PNG/JPEG/WebP mask image (plain or `data:` URI); any non-zero pixel is foreground
  - COCO RLE: {"counts": [...] | "<compressed string>", "size": [h, w]}
  - polygons: flat [x1, y1, ...], list of flat polygons, or list of [[x, y], ...] (pixel or 0-1 coords)
  - raw nested 0/1 lists of shape (h, w), or an ndarray
  - dict wrappers: {"mask" | "segmentation" | "rle" | "polygon(s)" | "png" | "data" | ...: <any of the above>}
"""
from __future__ import annotations

import base64
import binascii
import io
from typing import Any

import cv2
import numpy as np
from PIL import Image

from app.core.errors import MetaApiError

_WRAPPER_KEYS = ("mask", "segmentation", "rle", "polygons", "polygon", "png", "image", "data", "base64", "b64_json")
_MAX_ASPECT_DRIFT = 0.01  # a mask at another resolution must have the same aspect ratio


# ---- COCO RLE (compressed string form is the pycocotools/LEB128-like encoding) ----------------------

def rle_string_to_counts(s: str) -> list[int]:
    counts: list[int] = []
    p = 0
    while p < len(s):
        x, k, more = 0, 0, True
        while more:
            c = ord(s[p]) - 48
            x |= (c & 0x1F) << (5 * k)
            more = bool(c & 0x20)
            p += 1
            k += 1
            if not more and (c & 0x10):
                x |= -1 << (5 * k)
        if len(counts) > 2:
            x += counts[-2]
        counts.append(x)
    return counts


def counts_to_rle_string(counts: list[int]) -> str:
    out: list[str] = []
    for i, x in enumerate(counts):
        if i > 2:
            x -= counts[i - 2]
        more = True
        while more:
            c = x & 0x1F
            x >>= 5
            more = (x != -1) if (c & 0x10) else (x != 0)
            if more:
                c |= 0x20
            out.append(chr(c + 48))
    return "".join(out)


def mask_to_counts(mask: np.ndarray) -> list[int]:
    """Column-major run lengths starting with a zero run (COCO convention). Used by tests/tools."""
    flat = np.asarray(mask, dtype=bool).flatten(order="F")
    counts: list[int] = []
    cur, run = False, 0
    for v in flat:
        if bool(v) == cur:
            run += 1
        else:
            counts.append(run)
            cur, run = bool(v), 1
    counts.append(run)
    return counts


def decode_rle(rle: dict[str, Any]) -> np.ndarray:
    try:
        h, w = int(rle["size"][0]), int(rle["size"][1])
        counts = rle["counts"]
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise MetaApiError(f"Malformed RLE mask: {e}") from e
    if isinstance(counts, (bytes, bytearray)):
        counts = counts.decode()
    if isinstance(counts, str):
        counts = rle_string_to_counts(counts)
    if sum(counts) != h * w:
        raise MetaApiError(f"RLE run lengths sum to {sum(counts)}, expected {h * w}.")
    flat = np.zeros(h * w, dtype=bool)
    pos, val = 0, False
    for c in counts:
        if val:
            flat[pos : pos + c] = True
        pos += c
        val = not val
    return flat.reshape((w, h)).T  # column-major -> (h, w)


# ---- other encodings -------------------------------------------------------------------------------

def _decode_image_bytes(data: bytes) -> np.ndarray:
    try:
        img = Image.open(io.BytesIO(data))
        if img.mode in ("RGBA", "LA"):
            arr = np.asarray(img)[..., -1] if _alpha_is_informative(img) else np.asarray(img.convert("L"))
        else:
            arr = np.asarray(img.convert("L"))
    except Exception as e:  # noqa: BLE001
        raise MetaApiError(f"Mask image could not be decoded: {e}") from e
    return arr > 0


def _alpha_is_informative(img: Image.Image) -> bool:
    a = np.asarray(img)[..., -1]
    return bool(a.min() == 0 and a.max() > 0 and not np.all(np.asarray(img.convert("L")) == 0))


def _decode_b64_string(s: str) -> np.ndarray:
    if s.startswith("data:"):
        s = s.split(",", 1)[1] if "," in s else ""
    try:
        raw = base64.b64decode(s, validate=False)
    except (binascii.Error, ValueError) as e:
        raise MetaApiError(f"Mask string is not valid base64: {e}") from e
    return _decode_image_bytes(raw)


def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float, np.integer, np.floating)) and not isinstance(x, bool)


def _polygons_from(obj: list, h: int, w: int) -> list[np.ndarray]:
    """Normalise the accepted polygon layouts into a list of Nx2 float arrays (pixel coords)."""
    if obj and all(_is_number(v) for v in obj):  # one flat polygon
        polys = [obj]
    elif obj and all(isinstance(p, (list, tuple)) and len(p) == 2 and all(_is_number(v) for v in p) for p in obj):
        polys = [[v for pt in obj for v in pt]]  # one polygon as [[x, y], ...]
    else:
        polys = []
        for p in obj:
            if p and all(_is_number(v) for v in p):
                polys.append(p)
            elif p and all(isinstance(pt, (list, tuple)) and len(pt) == 2 for pt in p):
                polys.append([v for pt in p for v in pt])
            else:
                raise MetaApiError("Unrecognised polygon layout in mask.")
    out = []
    for p in polys:
        if len(p) < 6 or len(p) % 2:
            raise MetaApiError("A polygon needs at least 3 (x, y) points.")
        pts = np.asarray(p, dtype=np.float64).reshape(-1, 2)
        if pts.max() <= 1.0:  # normalised coordinates
            pts = pts * np.array([w, h], dtype=np.float64)
        out.append(pts)
    return out


def _rasterise(polys: list[np.ndarray], h: int, w: int) -> np.ndarray:
    m = np.zeros((h, w), dtype=np.uint8)
    for pts in polys:
        cv2.fillPoly(m, [np.round(pts).astype(np.int32)], 1)
    return m.astype(bool)


def _fit(mask: np.ndarray, h: int, w: int) -> np.ndarray:
    if mask.shape == (h, w):
        return mask
    mh, mw = mask.shape
    if abs((mw / mh) - (w / h)) / (w / h) > _MAX_ASPECT_DRIFT:
        raise MetaApiError(f"Mask shape {mask.shape} does not match image shape {(h, w)} (different aspect ratio).")
    return cv2.resize(mask.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)


def decode_mask(obj: Any, height: int, width: int) -> np.ndarray:
    """Decode one mask to a bool array of shape (height, width), or raise MetaApiError."""
    if isinstance(obj, np.ndarray):
        if obj.ndim != 2:
            raise MetaApiError(f"Mask array must be 2-D, got {obj.shape}.")
        return _fit(obj.astype(bool), height, width)
    if isinstance(obj, (bytes, bytearray)):
        return _fit(_decode_image_bytes(bytes(obj)), height, width)
    if isinstance(obj, str):
        return _fit(_decode_b64_string(obj.strip()), height, width)
    if isinstance(obj, dict):
        if "counts" in obj and "size" in obj:
            return _fit(decode_rle(obj), height, width)
        for key in _WRAPPER_KEYS:
            if key in obj and obj[key] is not None:
                return decode_mask(obj[key], height, width)
        raise MetaApiError(f"Unrecognised mask object with keys {sorted(obj)[:8]}.")
    if isinstance(obj, list):
        if not obj:
            return np.zeros((height, width), dtype=bool)
        if len(obj) == height and all(isinstance(r, (list, tuple)) and len(r) == width for r in obj) \
                and all(_is_number(v) or isinstance(v, bool) for v in obj[0]):
            return np.asarray(obj, dtype=bool)  # raw raster
        return _rasterise(_polygons_from(obj, height, width), height, width)
    raise MetaApiError(f"Unsupported mask type {type(obj).__name__}.")
