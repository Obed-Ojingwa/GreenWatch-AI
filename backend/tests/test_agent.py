from datetime import timedelta

import numpy as np
import pytest
from PIL import Image as PILImage

from app.db.models import AgentStep, AnalysisRun, InspectionTask, Report, SegmentationResult
from app.services import agent as agent_mod
from app.services.agent import execute_run, mark_stale_runs
from tests.helpers import FakeMeta, make_investigation, make_pair, new_run, scene
from app.services.validation import encode_png


def _go(db, storage, meta, categories=("buildings",), pair=None, georef=None, approval=False):
    e, n = pair or make_pair(1)
    inv = make_investigation(db, storage, e, n, categories, georef=georef)
    run = new_run(db, inv, categories, approval)
    execute_run(db, run.id, meta, storage)
    db.expire_all()
    return inv, db.get(AnalysisRun, run.id)


def _steps(db, run):
    return [(s.tool, s.status) for s in db.query(AgentStep).filter_by(run_id=run.id).order_by(AgentStep.idx)]


def test_happy_path_completed_with_full_report(db, storage):
    meta = FakeMeta()
    inv, run = _go(db, storage, meta)
    assert run.status == "completed", run.error
    tools = [t for t, _ in _steps(db, run)]
    assert tools == ["get_investigation", "validate_image_pair", "segment_image", "check_mask_quality",
                     "segment_image", "check_mask_quality", "compare_masks",
                     "calculate_change_metrics", "save_analysis_results", "submit_interpretation",
                     "generate_report"]
    m = run.metrics["buildings"]
    assert m["change_direction"] == "increase" and m["difference_pixels"] > 0
    assert m["new_pixels"] > m["old_pixels"] > 0
    assert m["metric_type"] == "pixel_count" and not m["real_world_area_available"]
    assert "area_m2" not in m  # test G: no georeference -> no m2
    assert run.interpretation["items"][0]["source"] == "muse_spark"
    rep = db.query(Report).filter_by(run_id=run.id).one()
    assert rep.kind == "full"
    assert storage.download_bytes(rep.storage_path)[:4] == b"%PDF"
    assert inv.status == "completed"


def test_metrics_match_independent_count(db, storage):
    meta = FakeMeta()
    _, run = _go(db, storage, meta)
    segs = {s.role: s for s in db.query(SegmentationResult).filter_by(run_id=run.id)}
    from app.services.segmentation import png_to_mask
    old = png_to_mask(storage.download_bytes(segs["earlier"].mask_path))
    new = png_to_mask(storage.download_bytes(segs["newer"].mask_path))
    valid = np.asarray(PILImage.open(__import__("io").BytesIO(storage.download_bytes(f"runs/{run.id}/prepared_newer.png")))).shape
    m = run.metrics["buildings"]
    assert m["old_pixels"] == int(old.sum()) - int((old & ~_valid(storage, run)).sum())
    assert m["new_pixels"] == int(new.sum()) - int((new & ~_valid(storage, run)).sum())


def _valid(storage, run):
    # recompute the overlap mask the same way the validator did
    from app.services.validation import prepare_pair
    e, n = make_pair(1)
    return prepare_pair(e, n).valid


def test_retry_path_trees_to_vegetation(db, storage):
    meta = FakeMeta()  # prompt "trees" returns nothing -> stats gate flags -> retry with "vegetation"
    _, run = _go(db, storage, meta, categories=("trees",))
    segs = db.query(SegmentationResult).filter_by(run_id=run.id, role="earlier").order_by(SegmentationResult.attempt).all()
    assert [(s.prompt_used, s.qc_status) for s in segs] == [("trees", "retry"), ("vegetation", "accepted")]
    assert run.status == "completed", run.error
    assert run.metrics["trees"]["prompts"]["earlier"] == "vegetation"
    assert run.metrics["trees"]["change_direction"] == "decrease"


def test_vision_rejection_exhausts_retries_and_flags(db, storage):
    meta = FakeMeta(vision=lambda p: (False, ["mask looks wrong"], None))
    _, run = _go(db, storage, meta)
    segs = db.query(SegmentationResult).filter_by(run_id=run.id, role="earlier").order_by(SegmentationResult.attempt).all()
    assert [s.qc_status for s in segs] == ["retry", "flagged"]  # initial + 1 retry
    assert run.status == "review_required"
    rep = db.query(Report).filter_by(run_id=run.id).one()
    assert rep.kind == "needs_review"
    assert any("flagged" in l for l in run.findings[0]["limitations"])


def test_e_unrelated_pair_stops_with_diagnostic_report(db, storage):
    pair = (encode_png(scene(1)), encode_png(scene(99)))
    meta = FakeMeta()
    inv, run = _go(db, storage, meta, pair=pair)
    assert run.status == "review_required"
    assert meta.segment_calls == 0 and meta.vision_calls == 0  # never spent credits on a bad pair
    assert run.metrics is None
    assert run.interpretation["items"][0]["source"] == "backend_template"
    assert db.query(Report).filter_by(run_id=run.id).one().kind == "diagnostic"
    assert [t for t, _ in _steps(db, run)] == ["get_investigation", "validate_image_pair"]


def test_f_meta_failure_marks_failed_and_no_report(db, storage):
    meta = FakeMeta(fail_segment=True)
    inv, run = _go(db, storage, meta)
    assert run.status == "failed" and "503" in run.error
    assert db.query(Report).filter_by(run_id=run.id).count() == 0
    assert inv.status == "failed"
    assert _steps(db, run)[-1] == ("segment_image", "error")


def test_h_bad_evidence_id_rejected_then_corrected(db, storage):
    _, run = _go(db, storage, FakeMeta(interp_mode="bad_evidence"))
    subs = [(t, s) for t, s in _steps(db, run) if t == "submit_interpretation"]
    assert subs == [("submit_interpretation", "error"), ("submit_interpretation", "ok")]
    assert run.interpretation["items"][0]["source"] == "muse_spark"


def test_illegality_wording_rejected(db, storage):
    _, run = _go(db, storage, FakeMeta(interp_mode="illegal"))
    step = db.query(AgentStep).filter_by(run_id=run.id, tool="submit_interpretation", status="error").one()
    assert "illegal" in step.output["error"].lower()
    assert "illegal" not in run.interpretation["items"][0]["summary"].lower()


def test_invented_number_rejected(db, storage):
    _, run = _go(db, storage, FakeMeta(interp_mode="invented_number"))
    step = db.query(AgentStep).filter_by(run_id=run.id, tool="submit_interpretation", status="error").one()
    assert "87" in step.output["error"]


def test_model_never_interprets_backend_fallback_is_logged(db, storage):
    _, run = _go(db, storage, FakeMeta(interp_mode="never"))
    assert run.status == "completed"
    assert run.interpretation["items"][0]["source"] == "backend_template"
    assert ("backend_fallback_interpretation", "ok") in _steps(db, run)
    assert db.query(Report).filter_by(run_id=run.id).one().kind == "full"


def test_cache_makes_reruns_free(db, storage):
    meta = FakeMeta()
    e, n = make_pair(1)
    inv = make_investigation(db, storage, e, n)
    r1 = new_run(db, inv)
    execute_run(db, r1.id, meta, storage)
    first = meta.segment_calls
    assert first == 2
    r2 = new_run(db, inv)
    execute_run(db, r2.id, meta, storage)
    db.expire_all()
    assert meta.segment_calls == first
    assert all(s.from_cache for s in db.query(SegmentationResult).filter_by(run_id=r2.id))
    assert db.get(AnalysisRun, r2.id).metrics == db.get(AnalysisRun, r1.id).metrics


def test_g_georeferenced_pair_allows_m2(db, storage):
    geo = {"crs": "EPSG:32631", "projected": True, "pixel_size_m": 0.5, "bounds": [0, 0, 300, 300]}
    _, run = _go(db, storage, FakeMeta(), georef=geo)
    m = run.metrics["buildings"]
    assert m["real_world_area_available"] is True
    # georef path: pixel size is the *image* pixel size; area scales with it
    assert m["area_m2"]["earlier"] == pytest.approx(m["old_pixels"] * 0.25)
    assert m["area_m2"]["difference"] == pytest.approx(m["difference_pixels"] * 0.25)


def test_inspection_task_requires_approval(db, storage):
    inv, run = _go(db, storage, FakeMeta())
    ctx = agent_mod.RunContext(db=db, run=run, inv=inv, meta=FakeMeta(), storage=storage,
                               settings=agent_mod.get_settings())
    out = agent_mod.run_tool(ctx, "create_inspection_task",
                             {"title": "Inspect", "reason": "Potential change", "priority": "high"})
    assert "error" in out and db.query(InspectionTask).count() == 0
    run.approval = True
    out = agent_mod.run_tool(ctx, "create_inspection_task",
                             {"title": "Inspect", "reason": "Potential change", "priority": "high"})
    assert "task_id" in out and db.query(InspectionTask).count() == 1


def test_tool_prerequisites_enforced(db, storage):
    e, n = make_pair(1)
    inv = make_investigation(db, storage, e, n)
    run = new_run(db, inv)
    ctx = agent_mod.RunContext(db=db, run=run, inv=inv, meta=FakeMeta(), storage=storage,
                               settings=agent_mod.get_settings())
    assert "validate_image_pair" in agent_mod.run_tool(ctx, "segment_image", {"role": "earlier", "category": "buildings"})["error"]
    assert "Unknown tool" in agent_mod.run_tool(ctx, "run_sql", {"q": "drop table x"})["error"]
    agent_mod.run_tool(ctx, "validate_image_pair", {})
    assert "not part of this run" in agent_mod.run_tool(ctx, "segment_image", {"role": "earlier", "category": "oceans"})["error"]
    assert "plain-text" in agent_mod.run_tool(ctx, "segment_image", {"role": "earlier", "category": "buildings", "prompt": "x; DROP TABLE"})["error"]
    assert "Invalid arguments" in agent_mod.run_tool(ctx, "segment_image", {"role": "middle", "category": "buildings"})["error"]
    assert "compare_masks" not in agent_mod.run_tool(ctx, "compare_masks", {"category": "buildings"}).get("evidence_id", "")


def test_stale_runs_are_failed_on_startup(db, storage):
    e, n = make_pair(1)
    inv = make_investigation(db, storage, e, n)
    run = new_run(db, inv)
    run.status = "processing"
    run.created_at = run.created_at - timedelta(minutes=30)
    db.commit()
    assert mark_stale_runs(db, 10) == 1
    db.expire_all()
    assert db.get(AnalysisRun, run.id).status == "failed"
