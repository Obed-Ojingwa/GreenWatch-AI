import httpx

from app.core.errors import DomainError
from app.services.storage import SupabaseStorage


def test_supabase_storage_upload_download_and_exists(monkeypatch):
    calls = []

    def post(url, **kwargs):
        calls.append(("post", url, kwargs))
        return httpx.Response(200, request=httpx.Request("POST", url))

    def get(url, **kwargs):
        calls.append(("get", url, kwargs))
        return httpx.Response(200, content=b"image-bytes", request=httpx.Request("GET", url))

    def head(url, **kwargs):
        calls.append(("head", url, kwargs))
        return httpx.Response(200, request=httpx.Request("HEAD", url))

    monkeypatch.setattr("app.services.storage.httpx.post", post)
    monkeypatch.setattr("app.services.storage.httpx.get", get)
    monkeypatch.setattr("app.services.storage.httpx.head", head)
    storage = SupabaseStorage("https://project.supabase.co/", "test-service-key", "greenwatch")

    storage.upload_bytes("runs/run-id/earlier image.png", b"image-bytes", "image/png")
    assert storage.download_bytes("runs/run-id/earlier image.png") == b"image-bytes"
    assert storage.exists("runs/run-id/earlier image.png")

    upload = calls[0]
    assert upload[0] == "post"
    assert upload[1].endswith("/object/greenwatch/runs/run-id/earlier%20image.png")
    assert upload[2]["headers"]["Content-Type"] == "image/png"
    assert upload[2]["headers"]["x-upsert"] == "true"
    assert calls[1][0] == "get" and calls[1][1].endswith("/object/greenwatch/runs/run-id/earlier%20image.png")
    assert calls[2][0] == "head" and calls[2][1].endswith("/object/greenwatch/runs/run-id/earlier%20image.png")


def test_supabase_signed_url_is_publicly_fetchable_token_not_service_key(monkeypatch):
    def post(url, **kwargs):
        assert url.endswith("/object/sign/greenwatch/reports/run-id/report.pdf")
        assert kwargs["json"] == {"expiresIn": 120}
        return httpx.Response(
            200,
            json={"signedURL": "/object/sign/greenwatch/reports/run-id/report.pdf?token=signed"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.services.storage.httpx.post", post)
    storage = SupabaseStorage("https://project.supabase.co", "test-service-key", "greenwatch")

    signed_url = storage.signed_url("reports/run-id/report.pdf", ttl=120)

    assert signed_url == (
        "https://project.supabase.co/storage/v1/object/sign/"
        "greenwatch/reports/run-id/report.pdf?token=signed"
    )
    assert "test-service-key" not in signed_url


def test_supabase_storage_upload_failure_is_reported_without_request_credentials(monkeypatch):
    def post(url, **kwargs):
        return httpx.Response(403, text="storage permission denied", request=httpx.Request("POST", url))

    monkeypatch.setattr("app.services.storage.httpx.post", post)
    storage = SupabaseStorage("https://project.supabase.co", "test-service-key", "greenwatch")

    try:
        storage.upload_bytes("smoke-tests/probe.txt", b"probe", "text/plain")
    except DomainError as exc:
        assert "403" in exc.detail
        assert "test-service-key" not in exc.detail
    else:
        raise AssertionError("Supabase upload failure was not surfaced")
