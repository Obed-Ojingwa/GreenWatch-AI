import base64
import io

import numpy as np
import pytest
from PIL import Image

from app.core.errors import MetaApiError
from app.services.mask_codec import (
    counts_to_rle_string, decode_mask, decode_rle, mask_to_counts, rle_string_to_counts,
)

H, W = 40, 60


def _target() -> np.ndarray:
    m = np.zeros((H, W), bool)
    m[5:20, 10:30] = True   # rectangle
    m[30:38, 40:58] = True  # second blob
    return m


def _png_b64(mask: np.ndarray, mode="L") -> str:
    buf = io.BytesIO()
    Image.fromarray(mask.astype(np.uint8) * 255, mode="L").convert(mode).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def test_png_base64_and_data_uri():
    t = _target()
    assert np.array_equal(decode_mask(_png_b64(t), H, W), t)
    assert np.array_equal(decode_mask("data:image/png;base64," + _png_b64(t), H, W), t)
    assert np.array_equal(decode_mask(_png_b64(t, "RGB"), H, W), t)


def test_png_alpha_mask_uses_alpha_channel():
    t = _target()
    rgba = np.zeros((H, W, 4), np.uint8)
    rgba[..., :3] = 255  # white everywhere; the information is in alpha
    rgba[..., 3] = t.astype(np.uint8) * 255
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG")
    assert np.array_equal(decode_mask(base64.b64encode(buf.getvalue()).decode(), H, W), t)


def test_rle_uncompressed_counts_roundtrip():
    t = _target()
    rle = {"size": [H, W], "counts": mask_to_counts(t)}
    assert np.array_equal(decode_mask(rle, H, W), t)


def test_rle_compressed_string_roundtrip():
    t = _target()
    counts = mask_to_counts(t)
    s = counts_to_rle_string(counts)
    assert rle_string_to_counts(s) == counts
    assert np.array_equal(decode_mask({"size": [H, W], "counts": s}, H, W), t)


def test_rle_compressed_handles_large_and_edge_runs():
    full = np.ones((H, W), bool)
    empty = np.zeros((H, W), bool)
    for m in (full, empty):
        c = mask_to_counts(m)
        assert np.array_equal(decode_rle({"size": [H, W], "counts": counts_to_rle_string(c)}), m)


def test_rle_with_wrong_total_is_rejected():
    with pytest.raises(MetaApiError, match="sum"):
        decode_mask({"size": [H, W], "counts": [5, 5]}, H, W)


def test_flat_polygon_and_polygon_lists():
    poly = [10, 5, 29, 5, 29, 19, 10, 19]
    m = decode_mask(poly, H, W)
    assert m[12, 20] and not m[30, 50]
    assert abs(int(m.sum()) - 15 * 20) < 80  # fillPoly includes the boundary
    pairs = [[10, 5], [29, 5], [29, 19], [10, 19]]
    assert np.array_equal(decode_mask(pairs, H, W), m)
    two = decode_mask([poly, [40, 30, 57, 30, 57, 37, 40, 37]], H, W)
    assert two[12, 20] and two[33, 50]


def test_normalised_polygon_coordinates_are_scaled():
    poly = [0.25, 0.25, 0.75, 0.25, 0.75, 0.75, 0.25, 0.75]
    m = decode_mask(poly, H, W)
    assert m[H // 2, W // 2] and not m[0, 0]


def test_raw_raster_list_and_ndarray():
    t = _target()
    assert np.array_equal(decode_mask(t.astype(int).tolist(), H, W), t)
    assert np.array_equal(decode_mask(t, H, W), t)


def test_dict_wrappers():
    t = _target()
    assert np.array_equal(decode_mask({"mask": _png_b64(t), "score": 0.9}, H, W), t)
    assert np.array_equal(decode_mask({"segmentation": {"size": [H, W], "counts": mask_to_counts(t)}}, H, W), t)
    assert np.array_equal(decode_mask({"rle": {"size": [H, W], "counts": mask_to_counts(t)}}, H, W), t)


def test_resizes_same_aspect_but_rejects_different_aspect():
    t = _target()
    big = np.kron(t, np.ones((2, 2), bool))  # 80 x 120, same aspect
    out = decode_mask(big, H, W)
    assert out.shape == (H, W) and np.array_equal(out, t)
    with pytest.raises(MetaApiError, match="aspect"):
        decode_mask(np.ones((H, W + 20), bool), H, W)


def test_empty_list_is_an_empty_mask():
    m = decode_mask([], H, W)
    assert m.shape == (H, W) and not m.any()


@pytest.mark.parametrize("bad", [{"foo": 1}, 12345, "###notbase64###", "aGVsbG8=", [[1, 2], [3]], [1, 2, 3]])
def test_garbage_raises_meta_api_error(bad):
    with pytest.raises(MetaApiError):
        decode_mask(bad, H, W)
