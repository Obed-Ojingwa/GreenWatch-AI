"""Image-pair validation and alignment.

Hierarchy (strict): georeferenced overlap -> ORB + RANSAC homography -> reject.
One global transform plus an overlap check; no tile-level alignment.
A failed check yields `review_required` or `reject`; the agent cannot override either.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np
from PIL import Image
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.errors import ValidationFailed
from app.services.comparison import Georef

Verdict = Literal["ok", "review_required", "reject"]


class PairValidation(BaseModel):
    verdict: Verdict
    aligned: bool
    method: str  # georef | orb_homography | none
    inlier_ratio: float | None = None
    inliers: int | None = None
    good_matches: int | None = None
    overlap_ratio: float | None = None
    scale: float | None = None
    rotation_deg: float | None = None
    grid: tuple[int, int] | None = None  # (height, width) of the analysis grid
    georef: Georef | None = None
    warnings: list[str] = []
    reasons: list[str] = []  # why a non-ok verdict was given


@dataclass
class PreparedPair:
    earlier: np.ndarray  # HxWx3 uint8 on the analysis grid
    newer: np.ndarray  # HxWx3 uint8 warped/resized onto the same grid
    valid: np.ndarray  # HxW bool overlap region
    validation: PairValidation


def decode_image(data: bytes) -> np.ndarray:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        return np.asarray(img.convert("RGB"))
    except Exception as e:  # noqa: BLE001
        raise ValidationFailed(f"Could not decode image: {e}") from e


def encode_png(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def _grid_size(h: int, w: int, max_dim: int) -> tuple[int, int]:
    s = min(1.0, max_dim / max(h, w))
    return max(1, round(h * s)), max(1, round(w * s))


def _georef_from_meta(meta: dict | None) -> Georef | None:
    if not meta:
        return None
    return Georef(
        crs=meta.get("crs"), projected=bool(meta.get("projected")), pixel_size_m=meta.get("pixel_size_m")
    )


def _try_georef(
    e: np.ndarray, n: np.ndarray, em: dict | None, nm: dict | None, grid: tuple[int, int], v: PairValidation
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """If both images carry bounds in the same CRS, crop both to their intersection."""
    if not (em and nm and em.get("bounds") and nm.get("bounds") and em.get("crs") and em.get("crs") == nm.get("crs")):
        return None
    ex0, ey0, ex1, ey1 = em["bounds"]
    nx0, ny0, nx1, ny1 = nm["bounds"]
    ix0, iy0, ix1, iy1 = max(ex0, nx0), max(ey0, ny0), min(ex1, nx1), min(ey1, ny1)
    if ix1 <= ix0 or iy1 <= iy0:
        v.reasons.append("Georeferenced bounds do not intersect.")
        return None
    inter = (ix1 - ix0) * (iy1 - iy0)
    overlap = inter / min((ex1 - ex0) * (ey1 - ey0), (nx1 - nx0) * (ny1 - ny0))
    v.overlap_ratio = float(overlap)

    def crop(img: np.ndarray, b: tuple[float, float, float, float]) -> np.ndarray:
        x0, y0, x1, y1 = b
        h, w = img.shape[:2]
        c0 = int(round((ix0 - x0) / (x1 - x0) * w))
        c1 = int(round((ix1 - x0) / (x1 - x0) * w))
        r0 = int(round((y1 - iy1) / (y1 - y0) * h))  # image rows run north -> south
        r1 = int(round((y1 - iy0) / (y1 - y0) * h))
        return img[max(r0, 0) : max(r1, r0 + 1), max(c0, 0) : max(c1, c0 + 1)]

    ec, nc = crop(e, (ex0, ey0, ex1, ey1)), crop(n, (nx0, ny0, nx1, ny1))
    gh, gw = _grid_size(ec.shape[0], ec.shape[1], get_settings().max_image_dim)
    ec = cv2.resize(ec, (gw, gh), interpolation=cv2.INTER_AREA)
    nc = cv2.resize(nc, (gw, gh), interpolation=cv2.INTER_AREA)
    v.grid = (gh, gw)
    # The analysis grid is a resampled version of the ground footprint, so its pixel size is the
    # footprint width / grid width (in CRS units, which extract_georef guarantees are metres when
    # pixel_size_m is set). Using the original image's pixel size here would misstate every area.
    if v.georef and v.georef.pixel_size_m:
        px_x, px_y = (ix1 - ix0) / gw, (iy1 - iy0) / gh
        if abs(px_x - px_y) / max(px_x, px_y) <= 0.02:
            v.georef = v.georef.model_copy(update={"pixel_size_m": (px_x + px_y) / 2})
        else:
            v.georef = v.georef.model_copy(update={"pixel_size_m": None})
            v.warnings.append("Analysis-grid pixels are not square on the ground; area in m2 is not reported.")
    return ec, nc, np.ones((gh, gw), dtype=bool)


def prepare_pair(
    earlier_bytes: bytes,
    newer_bytes: bytes,
    earlier_meta: dict | None = None,
    newer_meta: dict | None = None,
    has_dates: tuple[bool, bool] = (True, True),
) -> PreparedPair:
    s = get_settings()
    e_full, n_full = decode_image(earlier_bytes), decode_image(newer_bytes)
    v = PairValidation(verdict="ok", aligned=False, method="none")
    v.georef = _georef_from_meta(earlier_meta)

    if not all(has_dates):
        v.warnings.append("Capture date missing for at least one image; temporal claims are limited.")
    eh, ew = e_full.shape[:2]
    nh, nw = n_full.shape[:2]
    if abs(eh / ew - nh / nw) > 0.05:
        v.warnings.append("Images have different aspect ratios; comparison may be unreliable.")

    # 1) Georeferenced path
    geo = _try_georef(e_full, n_full, earlier_meta, newer_meta, (eh, ew), v)
    if geo is not None:
        ec, nc, valid = geo
        v.aligned, v.method = True, "georef"
        if v.overlap_ratio is not None and v.overlap_ratio < s.align_min_overlap:
            v.verdict = "review_required"
            v.reasons.append(f"Georeferenced overlap {v.overlap_ratio:.0%} below {s.align_min_overlap:.0%}.")
        return PreparedPair(ec, nc, valid, v)

    # 2) ORB + RANSAC homography
    gh, gw = _grid_size(eh, ew, s.max_image_dim)
    e = cv2.resize(e_full, (gw, gh), interpolation=cv2.INTER_AREA)
    n_scaled = cv2.resize(n_full, (gw, gh), interpolation=cv2.INTER_AREA)
    v.grid = (gh, gw)
    # The earlier image was resampled onto the grid: scale its pixel size to match (see _try_georef).
    if v.georef and v.georef.pixel_size_m:
        v.georef = v.georef.model_copy(
            update={"pixel_size_m": v.georef.pixel_size_m * ((ew / gw) + (eh / gh)) / 2}
        )

    orb = cv2.ORB_create(nfeatures=4000)
    ge, gn = cv2.cvtColor(e, cv2.COLOR_RGB2GRAY), cv2.cvtColor(n_scaled, cv2.COLOR_RGB2GRAY)
    ke, de = orb.detectAndCompute(ge, None)
    kn, dn = orb.detectAndCompute(gn, None)
    good: list = []
    if de is not None and dn is not None and len(ke) >= 8 and len(kn) >= 8:
        bf = cv2.BFMatcher(cv2.NORM_HAMMING)
        for pair in bf.knnMatch(dn, de, k=2):
            if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance:
                good.append(pair[0])
    v.good_matches = len(good)

    if len(good) < 8:
        v.verdict = "reject"
        v.reasons.append(
            f"Only {len(good)} feature matches: cannot establish that both images show the same site."
        )
        return PreparedPair(e, n_scaled, np.ones((gh, gw), bool), v)

    src = np.float32([kn[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([ke[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    H, inl = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    if H is None or inl is None:
        v.verdict = "reject"
        v.reasons.append("Could not estimate a transform between the images.")
        return PreparedPair(e, n_scaled, np.ones((gh, gw), bool), v)

    H = H / H[2, 2]
    inliers = int(inl.sum())
    v.inliers, v.inlier_ratio = inliers, inliers / len(good)
    det = abs(np.linalg.det(H[:2, :2]))
    v.scale = float(math.sqrt(det)) if det > 0 else 0.0
    v.rotation_deg = float(math.degrees(math.atan2(H[1, 0], H[0, 0])))

    warped = cv2.warpPerspective(n_scaled, H, (gw, gh))
    ones = cv2.warpPerspective(np.full((gh, gw), 255, np.uint8), H, (gw, gh))
    valid = cv2.erode(ones, np.ones((3, 3), np.uint8)) > 0
    v.overlap_ratio = float(valid.mean())

    if v.overlap_ratio < 0.3:
        v.verdict = "reject"
        v.reasons.append(f"Images overlap only {v.overlap_ratio:.0%}; they do not cover the same area.")
        return PreparedPair(e, warped, valid, v)

    # No geometric consensus, or a physically absurd scale: the images almost certainly do not
    # show the same site. Stop rather than compare.
    no_consensus = inliers < s.align_min_inliers and v.inlier_ratio < s.align_min_inlier_ratio
    absurd_scale = not (0.5 <= v.scale <= 2.0)
    if no_consensus or absurd_scale:
        v.verdict = "reject"
        v.method = "orb_homography"
        v.reasons.append(
            "No consistent geometric match between the images "
            f"({inliers} inliers, {v.inlier_ratio:.0%} of matches, scale {v.scale:.2f}x); "
            "they do not appear to show the same site."
        )
        return PreparedPair(e, warped, valid, v)

    problems: list[str] = []
    if inliers < s.align_min_inliers:
        problems.append(f"Only {inliers} geometric inliers (min {s.align_min_inliers}).")
    if v.inlier_ratio < s.align_min_inlier_ratio:
        problems.append(f"Inlier ratio {v.inlier_ratio:.0%} below {s.align_min_inlier_ratio:.0%}.")
    if abs(v.scale - 1.0) > s.align_max_scale_dev:
        problems.append(f"Implausible scale change ({v.scale:.2f}x).")
    if abs(v.rotation_deg) > s.align_max_rotation_deg:
        problems.append(f"Implausible rotation ({v.rotation_deg:.1f} degrees).")
    if abs(H[2, 0]) > 1e-3 or abs(H[2, 1]) > 1e-3:
        problems.append("Strong perspective distortion in the estimated transform.")
    if v.overlap_ratio < s.align_min_overlap:
        problems.append(f"Overlap {v.overlap_ratio:.0%} below {s.align_min_overlap:.0%}.")

    if problems:
        v.verdict, v.reasons = "review_required", problems
        v.aligned = False
    else:
        v.aligned = True
    v.method = "orb_homography"
    return PreparedPair(e, warped, valid, v)
