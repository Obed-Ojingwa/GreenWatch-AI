"""Object storage. `local` backend for development, `supabase` for production.

Raw storage paths are never returned to clients; use signed_url().
"""
from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import httpx

from app.core.config import get_settings
from app.core.errors import DomainError, NotFound


class Storage(Protocol):
    def upload_bytes(self, path: str, data: bytes, content_type: str) -> None: ...
    def download_bytes(self, path: str) -> bytes: ...
    def exists(self, path: str) -> bool: ...
    def signed_url(self, path: str, ttl: int | None = None) -> str: ...


class LocalStorage:
    """Files on disk; URLs are HMAC-signed and served by /api/files (see routes/files.py)."""

    def __init__(self, root: str, secret: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.secret = secret.encode() or b"dev-secret"

    def _p(self, path: str) -> Path:
        p = (self.root / path).resolve()
        if self.root not in p.parents and p != self.root:
            raise DomainError("Invalid storage path.")
        return p

    def upload_bytes(self, path: str, data: bytes, content_type: str) -> None:
        p = self._p(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def download_bytes(self, path: str) -> bytes:
        p = self._p(path)
        if not p.is_file():
            raise NotFound(f"Stored file not found: {path}")
        return p.read_bytes()

    def exists(self, path: str) -> bool:
        return self._p(path).is_file()

    def sign(self, path: str, exp: int) -> str:
        return hmac.new(self.secret, f"{path}:{exp}".encode(), hashlib.sha256).hexdigest()

    def verify(self, path: str, exp: int, sig: str) -> bool:
        return exp >= time.time() and hmac.compare_digest(self.sign(path, exp), sig)

    def signed_url(self, path: str, ttl: int | None = None) -> str:
        exp = int(time.time()) + (ttl or get_settings().signed_url_ttl)
        return f"/api/files/{quote(path)}?exp={exp}&sig={self.sign(path, exp)}"


class SupabaseStorage:
    """Supabase Storage REST API (private bucket, service key stays on the backend)."""

    def __init__(self, url: str, key: str, bucket: str):
        self.base = url.rstrip("/") + "/storage/v1"
        self.bucket = bucket
        self.headers = {"Authorization": f"Bearer {key}", "apikey": key}

    def upload_bytes(self, path: str, data: bytes, content_type: str) -> None:
        r = httpx.post(
            f"{self.base}/object/{self.bucket}/{quote(path)}",
            content=data,
            headers={**self.headers, "Content-Type": content_type, "x-upsert": "true"},
            timeout=60,
        )
        if r.status_code >= 300:
            raise DomainError(f"Storage upload failed ({r.status_code}): {r.text[:200]}")

    def download_bytes(self, path: str) -> bytes:
        r = httpx.get(f"{self.base}/object/{self.bucket}/{quote(path)}", headers=self.headers, timeout=60)
        if r.status_code == 404:
            raise NotFound(f"Stored file not found: {path}")
        if r.status_code >= 300:
            raise DomainError(f"Storage download failed ({r.status_code})")
        return r.content

    def exists(self, path: str) -> bool:
        r = httpx.head(f"{self.base}/object/{self.bucket}/{quote(path)}", headers=self.headers, timeout=30)
        return r.status_code == 200

    def signed_url(self, path: str, ttl: int | None = None) -> str:
        r = httpx.post(
            f"{self.base}/object/sign/{self.bucket}/{quote(path)}",
            json={"expiresIn": ttl or get_settings().signed_url_ttl},
            headers=self.headers,
            timeout=30,
        )
        if r.status_code >= 300:
            raise DomainError(f"Could not sign URL ({r.status_code})")
        return self.base + r.json()["signedURL"]


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        s = get_settings()
        if s.storage_backend == "supabase":
            _storage = SupabaseStorage(s.supabase_url, s.supabase_service_key, s.storage_bucket)
        else:
            _storage = LocalStorage(s.local_storage_dir, s.demo_access_key)
    return _storage


def set_storage(storage: Storage | None) -> None:
    """Test hook."""
    global _storage
    _storage = storage
