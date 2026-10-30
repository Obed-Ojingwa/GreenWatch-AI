"""Regression tests for bugs found in the Phase 1-5 review (see docs/REMAINING.md, 'Review log')."""
import uuid

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.models import AnalysisRun, Report, SegmentationResult
from app.main import app
from app.services.agent import execute_run
from app.services.meta_client import set_meta_client
from app.services.validation import encode_png, prepare_pair
from tests.helpers import FakeMeta, make_investigation, make_pair, new_run, scene, warp

KEY = {"X-Demo-Key": "testkey"}


def _run(db, storage, meta, pair=None, categories=("buildings",), georef=None):
    e, n = pair or make_pair(1)
    inv = make_investigation(db, storage, e, n, categories, georef=georef)
    run = new_run(db, inv, categories)
    execute_run(db, run.id, meta, storage)
    db.expire_all()
    return inv, db.get(AnalysisRun, run.id)


# ---- 1. square metres must use the pixel size of the (possibly downscaled) analysis grid -------------

def test_georef_path_rescales_pixel_size_when_grid_is_downscaled(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_image_dim", 300)  # 600 px images -> 300 px grid
    a = encode_png(scene(4))
    meta = {"crs": "EPSG:32631", "projected": True, "pixel_size_m": 1.0, "bounds": [0, 0, 600, 600]}
    v = prepare_pair(a, a, meta, dict(meta)).validation
    assert v.method == "georef" and v.grid == (300, 300)
    assert v.georef.pixel_size_m == pytest.approx(2.0)  # 600 m footprint / 300 px


def test_orb_path_rescales_pixel_size_when_grid_is_downscaled(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_image_dim", 300)
    a = scene(5)
    meta = {"crs": "EPSG:32631", "projected": True, "pixel_size_m": 1.0, "bounds": [0, 0, 600, 600]}
    # newer has no georeference, so the ORB path is used; areas are still on the earlier grid
    v = prepare_pair(encode_png(a), encode_png(warp(a)), meta, None).validation
    assert v.method == "orb_homography"
    assert v.georef.pixel_size_m == pytest.approx(2.0)


def test_end_to_end_area_uses_grid_pixel_size(db, storage, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_image_dim", 300)
    geo = {"crs": "EPSG:32631", "projected": True, "pixel_size_m": 1.0, "bounds": [0, 0, 600, 600]}
    _, run = _run(db, storage, FakeMeta(), georef=geo)
    m = run.metrics["buildings"]
    assert m["real_world_area_available"]
    assert m["area_m2"]["earlier"] == pytest.approx(m["old_pixels"] * 4.0)  # (2 m)^2 per grid pixel
    assert m["area_m2"]["newer"] == pytest.approx(m["new_pixels"] * 4.0)


# ---- 2. re-uploading an image must not destroy the evidence of earlier runs ---------------------------

def test_cannot_replace_image_after_a_run(db):
    set_meta_client(FakeMeta())
    client = TestClient(app)
    inv = client.post("/api/investigations", headers=KEY,
                      json={"title": "t", "location_name": "l", "objective": "o"}).json()
    e, n = make_pair(1)
    for role, data in (("earlier", e), ("newer", n)):
        r = client.post(f"/api/investigations/{inv['id']}/images", headers=KEY, data={"role": role},
                        files={"file": (f"{role}.png", data, "image/png")})
        assert r.status_code == 201
    rid = client.post(f"/api/investigations/{inv['id']}/agent-runs", json={}, headers=KEY).json()["id"]
    before = db.query(SegmentationResult).filter_by(run_id=uuid.UUID(rid)).count()
    assert before == 2
    r = client.post(f"/api/investigations/{inv['id']}/images", headers=KEY, data={"role": "earlier"},
                    files={"file": ("again.png", e, "image/png")})
    assert r.status_code == 409 and "evidence" in r.json()["detail"]
    db.expire_all()
    assert db.query(SegmentationResult).filter_by(run_id=uuid.UUID(rid)).count() == before


# ---- 3. no direction of change without verified alignment --------------------------------------------

def test_unverified_alignment_reports_direction_undetermined(db, storage):
    a, b = scene(7), scene(7)
    import cv2
    for x, y in [(60, 60), (200, 80), (400, 120)]:
        cv2.rectangle(a, (x, y), (x + 50, y + 50), (255, 0, 255), -1)
    b = a.copy()
    for x, y in [(300, 300), (450, 420)]:
        cv2.rectangle(b, (x, y), (x + 50, y + 50), (255, 0, 255), -1)
    pair = (encode_png(a), encode_png(warp(b, angle=25.0, scale=1.0, tx=0, ty=0)))
    assert prepare_pair(*pair).validation.verdict == "review_required"  # rotation beyond tolerance
    _, run = _run(db, storage, FakeMeta(), pair=pair)
    m = run.metrics["buildings"]
    assert run.status == "review_required"
    assert m["change_direction"] == "undetermined" and m["change_direction_raw"] in ("increase", "decrease", "unchanged")
    assert run.findings[0]["change_direction"] == "undetermined"
    assert any("not verified" in l for l in run.findings[0]["limitations"])
    assert db.query(Report).filter_by(run_id=run.id).one().kind == "needs_review"


# ---- 4. a retry that changes the prompt for one image only is not like-for-like ----------------------

def test_prompt_mismatch_forces_review(db, storage):
    calls = {"n": 0}

    def vision(_prompt):
        calls["n"] += 1
        return (False, ["outline misses most trees"], "vegetation") if calls["n"] == 1 else (True, [], None)

    _, run = _run(db, storage, FakeMeta(trees_empty=False, vision=vision), categories=("trees",))
    segs = db.query(SegmentationResult).filter_by(run_id=run.id).order_by(
        SegmentationResult.role, SegmentationResult.attempt).all()
    assert [(s.role, s.prompt_used) for s in segs] == [
        ("earlier", "trees"), ("earlier", "vegetation"), ("newer", "trees")]
    m = run.metrics["trees"]
    assert m["prompt_mismatch"] is True
    assert run.status == "review_required"
    assert any("like-for-like" in l for l in run.findings[0]["limitations"])
    assert db.query(Report).filter_by(run_id=run.id).one().kind == "needs_review"


def test_matching_prompts_stay_completed(db, storage):
    _, run = _run(db, storage, FakeMeta())
    assert run.status == "completed" and not run.metrics["buildings"].get("prompt_mismatch")
