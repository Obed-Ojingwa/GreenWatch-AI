import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image, TiffImagePlugin
from sqlalchemy.exc import OperationalError

from app.core.config import get_settings
from app.core.errors import MetaApiError
from app.db.models import AnalysisRun, Investigation
from app.main import app
from app.services.georef import extract_georef
from app.services.meta_client import ChatResponse, set_meta_client
from tests.helpers import FakeMeta, make_pair

KEY = {"X-Demo-Key": "testkey"}


@pytest.fixture
def client():
    return TestClient(app)


def _create(client, **over):
    body = {"title": "Ikoyi canopy check", "location_name": "Ikoyi, Lagos", "objective": "Investigate potential change"} | over
    r = client.post("/api/investigations", json=body, headers=KEY)
    assert r.status_code == 201, r.text
    return r.json()


def _upload(client, inv_id, role, data, ctype="image/png", date="2023-01-01"):
    return client.post(f"/api/investigations/{inv_id}/images", headers=KEY,
                       data={"role": role, "capture_date": date, "source": "synthetic"},
                       files={"file": (f"{role}.png", data, ctype)})


def _ready(client):
    inv = _create(client)
    e, n = make_pair(1)
    assert _upload(client, inv["id"], "earlier", e).status_code == 201
    r = _upload(client, inv["id"], "newer", n, date="2024-01-01")
    assert r.status_code == 201
    return r.json()


def test_j_write_routes_need_key(client):
    body = {"title": "x", "location_name": "y", "objective": "z"}
    assert client.post("/api/investigations", json=body).status_code == 401
    assert client.post("/api/investigations", json=body, headers={"X-Demo-Key": "wrong"}).status_code == 401
    fake = "00000000-0000-0000-0000-000000000000"
    assert client.post(f"/api/investigations/{fake}/agent-runs", json={}).status_code == 401
    assert client.post(f"/api/agent-runs/{fake}/approve-task", json={"title": "t", "reason": "r"}).status_code == 401
    assert client.patch(f"/api/tasks/{fake}", json={"status": "done"}).status_code == 401
    assert client.post(f"/api/agent-runs/{fake}/report").status_code == 401
    assert client.post(f"/api/investigations/{fake}/images", data={"role": "earlier"},
                       files={"file": ("a.png", b"x", "image/png")}).status_code == 401


def test_meta_diagnostic_requires_separate_key_and_reports_effective_config(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "meta_diagnostic_key", "diagnostic-key")
    monkeypatch.setattr(settings, "meta_api_key", "provider-key")
    monkeypatch.setattr(settings, "meta_base_url", "https://api.meta.ai/v1")
    monkeypatch.setattr(settings, "meta_chat_path", "chat/completions")
    monkeypatch.setattr(settings, "agent_model", "muse-spark-1.3")

    class PassingMeta:
        def chat(self, messages, *, model, tools=None, images=None, json_schema=None):
            assert messages == [{"role": "user", "content": "Reply with exactly the word OK."}]
            assert model == "muse-spark-1.3"
            return ChatResponse(content="OK")

    set_meta_client(PassingMeta())
    assert client.post("/api/meta/diagnostics").status_code == 401
    response = client.post(
        "/api/meta/diagnostics",
        headers={"X-Meta-Diagnostic-Key": "diagnostic-key"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "result": "reply_received",
        "provider_status": 200,
        "api_host": "api.meta.ai",
        "endpoint_path": "/v1/chat/completions",
        "model": "muse-spark-1.3",
    }
    assert "provider-key" not in response.text


def test_meta_diagnostic_returns_only_sanitized_provider_error(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "meta_diagnostic_key", "diagnostic-key")
    monkeypatch.setattr(settings, "meta_api_key", "provider-key")
    monkeypatch.setattr(settings, "meta_base_url", "https://api.meta.ai/v1")
    monkeypatch.setattr(settings, "meta_chat_path", "chat/completions")
    monkeypatch.setattr(settings, "agent_model", "muse-spark-1.3")

    class FailingMeta:
        def chat(self, messages, *, model, tools=None, images=None, json_schema=None):
            raise MetaApiError("must not be returned", 404, "model_not_found")

    set_meta_client(FailingMeta())
    response = client.post(
        "/api/meta/diagnostics",
        headers={"X-Meta-Diagnostic-Key": "diagnostic-key"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "result": "provider_error",
        "provider_status": 404,
        "provider_error_code": "model_not_found",
        "api_host": "api.meta.ai",
        "endpoint_path": "/v1/chat/completions",
        "model": "muse-spark-1.3",
    }
    assert "provider-key" not in response.text
    assert "must not be returned" not in response.text


def test_sample_public_private_hidden(client, db):
    sample = Investigation(title="Sample", location_name="Lagos", objective="o", is_sample=True)
    private = Investigation(title="Private", location_name="Lagos", objective="o")
    db.add_all([sample, private])
    db.commit()
    names = [i["title"] for i in client.get("/api/investigations").json()]
    assert names == ["Sample"]
    assert {i["title"] for i in client.get("/api/investigations", headers=KEY).json()} == {"Sample", "Private"}
    assert client.get(f"/api/investigations/{sample.id}").status_code == 200
    assert client.get(f"/api/investigations/{private.id}").status_code == 401
    assert client.get(f"/api/investigations/{private.id}", headers=KEY).status_code == 200
    # sample is read-only even for key holders
    r = _upload(client, str(sample.id), "earlier", make_pair(1)[0])
    assert r.status_code == 400 and "read-only" in r.json()["detail"]
    assert client.post(f"/api/investigations/{sample.id}/agent-runs", json={}, headers=KEY).status_code == 400


def test_full_flow_over_http(client):
    set_meta_client(FakeMeta())
    inv = _ready(client)
    assert {i["role"] for i in inv["images"]} == {"earlier", "newer"}
    r = client.post(f"/api/investigations/{inv['id']}/agent-runs", json={"categories": ["buildings"]}, headers=KEY)
    assert r.status_code == 202 and r.json()["status"] == "queued"
    run = client.get(f"/api/agent-runs/{r.json()['id']}", headers=KEY).json()  # background task already ran
    assert run["status"] == "completed", run["error"]
    assert [s["tool"] for s in run["steps"]][-1] == "generate_report"
    assert run["metrics"]["buildings"]["change_direction"] == "increase"
    assert run["report"]["kind"] == "full"
    # signed urls work and return real content
    pdf = client.get(run["report"]["url"])
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    ov = client.get(run["segmentations"][0]["overlay_url"])
    assert ov.status_code == 200 and ov.headers["content-type"] == "image/png"
    assert client.get(run["prepared_urls"]["earlier"]).status_code == 200
    assert client.get(run["diff_urls"]["buildings"]).status_code == 200
    # a keyless client can't read a private run
    assert client.get(f"/api/agent-runs/{run['id']}").status_code == 401
    # approve -> task -> list -> status toggle
    t = client.post(f"/api/agent-runs/{run['id']}/approve-task", headers=KEY,
                    json={"title": "Inspect NE plot", "reason": "Potential change detected", "priority": "high"})
    assert t.status_code == 201
    tid = t.json()["id"]
    assert [x["id"] for x in client.get("/api/tasks", headers=KEY).json()] == [tid]
    assert client.get("/api/tasks").json() == []  # not visible without key
    assert client.patch(f"/api/tasks/{tid}", json={"status": "done"}, headers=KEY).json()["status"] == "done"
    assert client.get("/api/stats", headers=KEY).json() == {
        "investigations": 1, "awaiting_review": 0, "completed": 1, "open_tasks": 0}
    assert client.get(f"/api/investigations/{inv['id']}", headers=KEY).json()["latest_run_id"] == run["id"]


def test_upload_validation(client, monkeypatch):
    inv = _create(client)
    e, _ = make_pair(1)
    assert _upload(client, inv["id"], "earlier", e, ctype="application/pdf").status_code == 422
    assert _upload(client, inv["id"], "earlier", b"not an image").status_code == 422
    assert client.post(f"/api/investigations/{inv['id']}/images", headers=KEY, data={"role": "middle"},
                       files={"file": ("a.png", e, "image/png")}).status_code == 422
    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    assert _upload(client, inv["id"], "earlier", e).status_code == 400
    monkeypatch.undo()
    assert _upload(client, inv["id"], "earlier", e).status_code == 201
    # re-uploading a role replaces it rather than duplicating
    r = _upload(client, inv["id"], "earlier", e, date="2022-06-01").json()
    assert len([i for i in r["images"] if i["role"] == "earlier"]) == 1


def test_run_requires_both_images_and_blocks_concurrency(client, db):
    set_meta_client(FakeMeta())
    inv = _create(client)
    assert client.post(f"/api/investigations/{inv['id']}/agent-runs", json={}, headers=KEY).status_code == 422
    e, n = make_pair(1)
    _upload(client, inv["id"], "earlier", e)
    _upload(client, inv["id"], "newer", n)
    db.add(AnalysisRun(investigation_id=uuid.UUID(inv["id"]), status="processing"))
    db.commit()
    r = client.post(f"/api/investigations/{inv['id']}/agent-runs", json={}, headers=KEY)
    assert r.status_code == 409  # test J: concurrent run


def test_invalid_category_rejected(client):
    inv = _ready(client)
    assert client.post(f"/api/investigations/{inv['id']}/agent-runs", json={"categories": ["oceans"]}, headers=KEY).status_code == 422


def test_hourly_cap(client, monkeypatch):
    set_meta_client(FakeMeta())
    inv = _ready(client)
    monkeypatch.setattr(get_settings(), "max_agent_runs_per_hour", 0)
    r = client.post(f"/api/investigations/{inv['id']}/agent-runs", json={}, headers=KEY)
    assert r.status_code == 400 and "limit" in r.json()["detail"].lower()


def test_signed_urls_tamper_and_expiry(client, storage):
    storage.upload_bytes("x/a.png", b"abc", "image/png")
    url = storage.signed_url("x/a.png")
    assert client.get(url).status_code == 200
    assert client.get(url.replace("sig=", "sig=0")).status_code == 401
    assert client.get(url.replace("a.png", "b.png")).status_code == 401
    expired = storage.signed_url("x/a.png", ttl=-5)
    assert client.get(expired).status_code == 401
    assert client.get("/api/files/..%2F..%2Fetc%2Fpasswd?exp=9999999999&sig=x").status_code in (401, 404)


def test_report_not_generated_for_failed_run(client):
    set_meta_client(FakeMeta(fail_segment=True))
    inv = _ready(client)
    rid = client.post(f"/api/investigations/{inv['id']}/agent-runs", json={}, headers=KEY).json()["id"]
    run = client.get(f"/api/agent-runs/{rid}", headers=KEY).json()
    assert run["status"] == "failed" and run["report"] is None
    assert client.post(f"/api/agent-runs/{rid}/report", headers=KEY).status_code == 422  # test F/K
    assert client.post(f"/api/agent-runs/{rid}/approve-task", headers=KEY,
                       json={"title": "t", "reason": "r"}).status_code == 409


def test_health_and_cors(client):
    health = client.get("/health")
    assert health.json()["status"] == "ok"
    assert health.json()["database"] == "ready"
    r = client.options("/api/investigations", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-demo-key,content-type"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
    r = client.options("/api/investigations", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in r.headers
    for origin in (
        "https://greenwatch-ai-theta.vercel.app",
        "https://greenwatch-ai-670g.onrender.com",
    ):
        response = client.options("/api/investigations", headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        })
        assert response.headers.get("access-control-allow-origin") == origin


def test_health_reports_database_schema_failure_without_leaking_details(client, monkeypatch):
    class BrokenConnection:
        def __enter__(self):
            raise OperationalError("SELECT 1", {}, Exception("credential-safe test failure"))

        def __exit__(self, *_):
            return False

    monkeypatch.setattr("app.main.engine.connect", lambda: BrokenConnection())
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "environment": get_settings().environment,
        "database": "unavailable",
        "hint": "Verify PostgreSQL connectivity and apply the database schema.",
    }
    assert "password" not in response.text


def test_database_errors_return_cors_safe_sanitized_response(client):
    from app.db.session import get_db

    def broken_db():
        raise OperationalError("SELECT 1", {}, Exception("credential-safe test failure"))
        yield  # pragma: no cover

    app.dependency_overrides[get_db] = broken_db
    try:
        response = client.get("/api/stats", headers={"Origin": "http://localhost:5173"})
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 503
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.json() == {
        "error": "DatabaseUnavailable",
        "detail": "The database is unavailable. Verify PostgreSQL connectivity and schema setup.",
    }


def _geotiff(epsg, scale=0.5, linear_units=None):
    img = Image.new("RGB", (100, 80), (10, 20, 30))
    ifd = TiffImagePlugin.ImageFileDirectory_v2()
    ifd[33550] = (scale, scale, 0.0)
    ifd[33922] = (0.0, 0.0, 0.0, 500000.0, 4000000.0, 0.0)
    keys = [(1024, 0, 1, 1), (3072, 0, 1, epsg)]
    if linear_units:
        keys.append((3076, 0, 1, linear_units))
    ifd[34735] = (1, 1, 0, len(keys)) + tuple(v for k in keys for v in k)
    buf = io.BytesIO()
    img.save(buf, format="TIFF", tiffinfo=ifd)
    buf.seek(0)
    return Image.open(buf)


def test_geotiff_projected_metres_gives_pixel_size():
    g = extract_georef(_geotiff(32631))
    assert g["crs"] == "EPSG:32631" and g["projected"] and g["pixel_size_m"] == 0.5
    assert g["bounds"] == [500000.0, 4000000.0 - 80 * 0.5, 500000.0 + 100 * 0.5, 4000000.0]


def test_geotiff_unknown_units_refuses_pixel_size():
    g = extract_georef(_geotiff(2263))  # NY State Plane, feet: must NOT be treated as metres
    assert g["projected"] and g["pixel_size_m"] is None


def test_plain_png_has_no_georef():
    assert extract_georef(Image.open(io.BytesIO(make_pair(1)[0]))) is None


def test_geotiff_upload_enables_m2_end_to_end(client):
    set_meta_client(FakeMeta())
    inv = _create(client)
    e, n = make_pair(1)
    for role, data in (("earlier", e), ("newer", n)):
        im = Image.open(io.BytesIO(data))
        ifd = TiffImagePlugin.ImageFileDirectory_v2()
        ifd[33550], ifd[33922] = (0.5, 0.5, 0.0), (0.0, 0.0, 0.0, 500000.0, 4000000.0, 0.0)
        ifd[34735] = (1, 1, 0, 2, 1024, 0, 1, 1, 3072, 0, 1, 32631)
        buf = io.BytesIO()
        im.save(buf, format="TIFF", tiffinfo=ifd)
        r = client.post(f"/api/investigations/{inv['id']}/images", headers=KEY, data={"role": role},
                        files={"file": (f"{role}.tif", buf.getvalue(), "image/tiff")})
        assert r.status_code == 201 and r.json()["images"][0]["georeferenced"]
    rid = client.post(f"/api/investigations/{inv['id']}/agent-runs", json={}, headers=KEY).json()["id"]
    run = client.get(f"/api/agent-runs/{rid}", headers=KEY).json()
    assert run["status"] == "completed", run["error"]
    m = run["metrics"]["buildings"]
    assert m["real_world_area_available"] and m["area_m2"]["earlier"] > 0
    assert run["validation"]["method"] == "georef"
