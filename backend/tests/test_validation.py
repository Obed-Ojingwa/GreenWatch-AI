import cv2
import numpy as np

from app.services.validation import encode_png, prepare_pair


def _scene(seed=0, size=600):
    rng = np.random.default_rng(seed)
    img = np.full((size, size, 3), 110, np.uint8)
    for _ in range(250):  # textured blobs so ORB has features
        x, y = rng.integers(0, size, 2)
        r = int(rng.integers(4, 22))
        c = tuple(int(v) for v in rng.integers(30, 230, 3))
        cv2.circle(img, (int(x), int(y)), r, c, -1)
        cv2.rectangle(img, (int(x), int(y)), (int(x) + r, int(y) + r), c, 1)
    return cv2.GaussianBlur(img, (3, 3), 0)


def _warp(img, angle=3.0, scale=1.03, tx=12, ty=-8):
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, scale)
    M[:, 2] += (tx, ty)
    return cv2.warpAffine(img, M, (w, h), borderMode=cv2.BORDER_REFLECT)


def test_aligned_pair_with_change_is_ok():
    a = _scene(1)
    b = _warp(a).copy()
    cv2.rectangle(b, (250, 250), (330, 330), (200, 40, 40), -1)  # a real change
    p = prepare_pair(encode_png(a), encode_png(b))
    v = p.validation
    assert v.verdict == "ok", v.reasons
    assert v.aligned and v.method == "orb_homography"
    assert v.inlier_ratio > 0.5 and v.overlap_ratio > 0.8
    assert p.earlier.shape == p.newer.shape == p.valid.shape[:2] + (3,)


def test_e_unrelated_pair_is_rejected():
    p = prepare_pair(encode_png(_scene(1)), encode_png(_scene(99)))
    assert p.validation.verdict == "reject"
    assert not p.validation.aligned


def test_e_implausible_transform_needs_review():
    a = _scene(2)
    b = _warp(a, angle=40.0, scale=1.0, tx=0, ty=0)
    v = prepare_pair(encode_png(a), encode_png(b)).validation
    assert v.verdict in ("review_required", "reject")
    assert not v.aligned


def test_missing_dates_warn():
    a = _scene(3)
    v = prepare_pair(encode_png(a), encode_png(_warp(a)), has_dates=(True, False)).validation
    assert any("date" in w.lower() for w in v.warnings)


def test_georef_bounds_intersection():
    a = _scene(4)
    meta = {"crs": "EPSG:32631", "projected": True, "pixel_size_m": 1.0, "bounds": [0, 0, 600, 600]}
    p = prepare_pair(encode_png(a), encode_png(a), meta, dict(meta))
    assert p.validation.method == "georef" and p.validation.verdict == "ok"


def test_undecodable_image_raises():
    import pytest
    from app.core.errors import ValidationFailed
    with pytest.raises(ValidationFailed):
        prepare_pair(b"not an image", encode_png(_scene(1)))
