"""Pure NumPy measurement logic. No model, DB or network access.

Rules enforced here (in code, not in prompts):
  * masks must have identical shape, otherwise ValidationFailed
  * percentage change is None when the earlier count is zero (absolute difference only)
  * square metres only with a projected CRS and a known positive pixel size
"""
from __future__ import annotations

from typing import Literal

import numpy as np
from pydantic import BaseModel

from app.core.errors import UnsupportedMeasurement, ValidationFailed

ChangeDirection = Literal["increase", "decrease", "unchanged", "undetermined"]

# Overlay colours (RGBA). They match the legend in frontend/src/pages/Workspace.tsx.
GAINED_RGBA = (34, 197, 94, 170)
LOST_RGBA = (239, 68, 68, 170)
BOTH_RGBA = (148, 163, 184, 110)


class Georef(BaseModel):
    """Georeference of the analysis grid. `projected` must be true and `pixel_size_m` known for metric areas."""

    crs: str | None = None
    projected: bool = False
    pixel_size_m: float | None = None


class ComparisonResult(BaseModel):
    old_pixels: int
    new_pixels: int
    difference_pixels: int
    change_percent: float | None
    change_direction: ChangeDirection
    metric_type: str = "pixel_count"
    valid_pixels: int
    coverage_old: float
    coverage_new: float


def _as_bool(mask: np.ndarray, name: str) -> np.ndarray:
    arr = np.asarray(mask)
    if arr.ndim != 2:
        raise ValidationFailed(f"{name} must be a 2-D mask, got shape {arr.shape}.")
    return arr.astype(bool)


def compare_masks(old_mask: np.ndarray, new_mask: np.ndarray, valid_mask: np.ndarray | None = None) -> ComparisonResult:
    old, new = _as_bool(old_mask, "old_mask"), _as_bool(new_mask, "new_mask")
    if old.shape != new.shape:
        raise ValidationFailed(f"Mask shapes differ: {old.shape} vs {new.shape}.")
    if valid_mask is None:
        valid = np.ones(old.shape, dtype=bool)
    else:
        valid = _as_bool(valid_mask, "valid_mask")
        if valid.shape != old.shape:
            raise ValidationFailed(f"Valid mask shape {valid.shape} does not match masks {old.shape}.")
        if not valid.any():
            raise ValidationFailed("The valid (overlap) mask is empty; there is nothing to compare.")

    valid_pixels = int(np.count_nonzero(valid))
    old_px = int(np.count_nonzero(old & valid))
    new_px = int(np.count_nonzero(new & valid))
    diff = new_px - old_px
    percent = (diff / old_px) * 100.0 if old_px > 0 else None

    if old_px == 0 and new_px == 0:
        direction: ChangeDirection = "undetermined"  # nothing detected in either image
    elif diff > 0:
        direction = "increase"
    elif diff < 0:
        direction = "decrease"
    else:
        direction = "unchanged"

    return ComparisonResult(
        old_pixels=old_px, new_pixels=new_px, difference_pixels=diff, change_percent=percent,
        change_direction=direction, valid_pixels=valid_pixels,
        coverage_old=old_px / valid_pixels, coverage_new=new_px / valid_pixels,
    )


def m2_supported(georef: Georef | None) -> bool:
    return bool(georef and georef.projected and georef.crs and georef.pixel_size_m and georef.pixel_size_m > 0)


def area_m2(pixels: int | float, georef: Georef | None) -> float:
    """Area for `pixels`, only when the data supports it; otherwise raises UnsupportedMeasurement."""
    if georef is None:
        raise UnsupportedMeasurement("No georeference available; square metres cannot be claimed.")
    if not georef.projected or not georef.crs:
        raise UnsupportedMeasurement("The georeference is not a projected CRS; square metres cannot be claimed.")
    if not georef.pixel_size_m or georef.pixel_size_m <= 0:
        raise UnsupportedMeasurement("Pixel size is unknown; square metres cannot be claimed.")
    return float(pixels) * georef.pixel_size_m**2


def diff_overlay(old_mask: np.ndarray, new_mask: np.ndarray) -> np.ndarray:
    """RGBA overlay (uint8, HxWx4): gained = green, lost = red, in both = grey. Transparent elsewhere."""
    old, new = _as_bool(old_mask, "old_mask"), _as_bool(new_mask, "new_mask")
    if old.shape != new.shape:
        raise ValidationFailed(f"Mask shapes differ: {old.shape} vs {new.shape}.")
    out = np.zeros((*old.shape, 4), dtype=np.uint8)
    out[old & new] = BOTH_RGBA
    out[new & ~old] = GAINED_RGBA
    out[old & ~new] = LOST_RGBA
    return out
