"""Test environment. Env vars are set BEFORE any app module is imported (settings and the DB engine are
created at import time). Tests never touch the network except the loopback mock Meta server."""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="greenwatch-tests-")
os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["LOCAL_STORAGE_DIR"] = f"{_TMP}/storage"
os.environ["DEMO_ACCESS_KEY"] = "testkey"
os.environ["FRONTEND_ORIGIN"] = "http://localhost:5173"
for _k in ("META_API_KEY", "META_BASE_URL", "META_SEGMENT_PATH", "META_SEGMENT_MASKS_PATH"):
    os.environ.pop(_k, None)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_state(tmp_path):
    """Empty database + isolated storage + real Meta client restored, for every test."""
    from app.core.config import get_settings
    from app.db import models  # noqa: F401  (registers tables)
    from app.db.session import Base, engine
    from app.services.meta_client import set_meta_client
    from app.services.storage import LocalStorage, set_storage

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    set_meta_client(None)
    set_storage(LocalStorage(str(tmp_path / "storage"), get_settings().demo_access_key))
    try:
        from app.api.routes.runs import limiter
        limiter.reset()
    except Exception:  # noqa: BLE001
        pass
    yield
    set_meta_client(None)
    set_storage(None)


@pytest.fixture
def db():
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def storage():
    from app.services.storage import get_storage

    return get_storage()
