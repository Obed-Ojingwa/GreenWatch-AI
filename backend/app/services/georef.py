"""Extract georeferencing from a GeoTIFF using Pillow's TIFF tags (no rasterio dependency).

Square metres are only ever allowed with a projected CRS in metres and a known pixel size, so
anything ambiguous returns `pixel_size_m = None`.
"""
from __future__ import annotations

from PIL import Image

MODEL_PIXEL_SCALE = 33550
MODEL_TIEPOINT = 33922
GEO_KEY_DIRECTORY = 34735

PROJECTED_CS_TYPE = 3072
GEOGRAPHIC_TYPE = 2048
PROJ_LINEAR_UNITS = 3076
METRE = 9001


def _is_metre_epsg(epsg: int) -> bool:
    return 32601 <= epsg <= 32660 or 32701 <= epsg <= 32760 or epsg == 3857


def extract_georef(img: Image.Image) -> dict | None:
    tags = getattr(img, "tag_v2", None)
    if tags is None or MODEL_PIXEL_SCALE not in tags or MODEL_TIEPOINT not in tags:
        return None
    try:
        sx, sy = float(tags[MODEL_PIXEL_SCALE][0]), float(tags[MODEL_PIXEL_SCALE][1])
        tp = tags[MODEL_TIEPOINT]
        i, j, _, x, y, _ = (float(v) for v in tp[:6])
    except Exception:  # noqa: BLE001
        return None
    w, h = img.size
    minx, maxy = x - i * sx, y + j * sy
    bounds = [minx, maxy - h * sy, minx + w * sx, maxy]

    keys: dict[int, int] = {}
    kd = tags.get(GEO_KEY_DIRECTORY)
    if kd:
        n = int(kd[3])
        for k in range(n):
            key_id, loc, _count, value = kd[4 + 4 * k : 8 + 4 * k]
            if loc == 0:
                keys[int(key_id)] = int(value)

    if PROJECTED_CS_TYPE in keys and keys[PROJECTED_CS_TYPE] not in (0, 32767):
        epsg = keys[PROJECTED_CS_TYPE]
        metres = keys.get(PROJ_LINEAR_UNITS) == METRE or _is_metre_epsg(epsg)
        square = abs(sx - sy) / max(sx, sy) < 0.01
        return {
            "crs": f"EPSG:{epsg}", "projected": True,
            "pixel_size_m": sx if (metres and square) else None, "bounds": bounds,
        }
    if GEOGRAPHIC_TYPE in keys:
        return {"crs": f"EPSG:{keys[GEOGRAPHIC_TYPE]}", "projected": False, "pixel_size_m": None, "bounds": bounds}
    return None
