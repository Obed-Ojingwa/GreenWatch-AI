"""HttpMetaClient over a real socket against the test-only mock server (see mock_meta_server.py)."""
import io
import types

import numpy as np
import pytest
from PIL import Image as PILImage

from app.core.config import get_settings
from app.core.errors import MetaApiError
from app.db.models import AnalysisRun, Report
from app.services import meta_client as mc
from app.services.agent import execute_run
from app.services.meta_client import HttpMetaClient, PathNotFound, clean_messages, extract_path, fill_template
from tests.helpers import FakeMeta, make_investigation, make_pair, new_run
from tests.mock_meta_server import MockState, RunningServer

SEG_BODY = '{"model": "{{model}}", "image": "{{image_b64}}", "prompt": "{{prompt}}"}'


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")  # the sandbox routes other traffic via a proxy
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    # Skip retry backoff, but only inside meta_client (patching time.sleep itself is global).
    monkeypatch.setattr(mc, "time", types.SimpleNamespace(sleep=lambda *_: None))


def _configure(monkeypatch, server, **over):
    s = get_settings()
    for k, v in {
        "meta_base_url": server.base_url, "meta_api_key": "k", "meta_auth_header": "Authorization",
        "meta_auth_scheme": "Bearer", "meta_segment_path": "segment", "meta_segment_body": SEG_BODY,
        "meta_segment_masks_path": "masks.*", "meta_max_retries": 3, **over,
    }.items():
        monkeypatch.setattr(s, k, v)


def _png(arr) -> bytes:
    buf = io.BytesIO()
    PILImage.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


# ---- pure helpers ----------------------------------------------------------------------------------

def test_extract_path_and_fill_template():
    data = {"results": [{"instances": [{"mask": "a"}, {"mask": "b"}]}], "empty": []}
    assert extract_path(data, "results.0.instances.*.mask") == ["a", "b"]
    assert extract_path(data, "empty.*") == []  # present but empty: "nothing detected"
    with pytest.raises(PathNotFound):
        extract_path(data, "masks.*")  # absent: a wrong path must not look like "nothing detected"
    with pytest.raises(PathNotFound):
        extract_path(data, "results.5")
    assert fill_template({"a": ["x{{p}}", 1], "b": {"c": "{{p}}"}}, {"{{p}}": "Q"}) == {"a": ["xQ", 1], "b": {"c": "Q"}}


def test_clean_messages_drops_nulls():
    out = clean_messages([{"role": "assistant", "content": None, "tool_calls": None},
                          {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]}])
    assert out[0] == {"role": "assistant", "content": ""}
    assert out[1] == {"role": "assistant", "tool_calls": [{"id": "1"}]}


# ---- SAM call --------------------------------------------------------------------------------------

@pytest.mark.parametrize("fmt", ["png", "rle_str", "wrapped"])
def test_segment_decodes_each_mask_encoding(monkeypatch, fmt):
    fake = FakeMeta()
    img = np.full((60, 80, 3), 110, np.uint8)
    img[10:30, 20:50] = (255, 0, 255)  # "buildings" in the fake's colour convention
    with RunningServer(MockState(fake, mask_format=fmt)) as srv:
        _configure(monkeypatch, srv)
        masks = HttpMetaClient().segment(_png(img), "buildings")
    assert len(masks) == 1 and masks[0].shape == (60, 80)
    assert int(masks[0].sum()) == 20 * 30


def test_segment_empty_detection_is_an_empty_list(monkeypatch):
    with RunningServer(MockState(FakeMeta())) as srv:
        _configure(monkeypatch, srv)
        assert HttpMetaClient().segment(_png(np.full((20, 20, 3), 110, np.uint8)), "buildings") == []


def test_segment_refuses_until_contract_is_configured(monkeypatch):
    _configure(monkeypatch, type("S", (), {"base_url": "http://127.0.0.1:1/v1"})(), meta_segment_path="")
    with pytest.raises(MetaApiError, match="contract is not configured"):
        HttpMetaClient().segment(b"x", "buildings")


def test_segment_wrong_masks_path_fails_loudly(monkeypatch):
    with RunningServer(MockState(FakeMeta())) as srv:
        _configure(monkeypatch, srv, meta_segment_masks_path="results.*")
        with pytest.raises(MetaApiError, match="META_SEGMENT_MASKS_PATH"):
            HttpMetaClient().segment(_png(np.full((20, 20, 3), 110, np.uint8)), "buildings")


# ---- transport -------------------------------------------------------------------------------------

def test_auth_header_is_configurable(monkeypatch):
    state = MockState(FakeMeta(), api_key="secret", auth_header="x-api-key", auth_scheme="")
    with RunningServer(state) as srv:
        _configure(monkeypatch, srv, meta_api_key="secret", meta_auth_header="x-api-key", meta_auth_scheme="")
        r = HttpMetaClient().chat([{"role": "system", "content": "s"}, {"role": "user", "content": "Categories to analyse: buildings."}],
                                  model="m")
        assert r.tool_calls and r.tool_calls[0].name == "get_investigation"
        _configure(monkeypatch, srv, meta_api_key="wrong", meta_auth_header="x-api-key", meta_auth_scheme="")
        with pytest.raises(MetaApiError, match="401"):
            HttpMetaClient().chat([{"role": "user", "content": "hi"}], model="m")


def test_retries_5xx_but_not_4xx(monkeypatch):
    state = MockState(FakeMeta(), fail_first_n_chat=2)
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "Categories to analyse: buildings."}]
    with RunningServer(state) as srv:
        _configure(monkeypatch, srv)
        assert HttpMetaClient().chat(msgs, model="m").tool_calls  # 503, 503, then 200
        assert state.chat_attempts == 3
    state = MockState(FakeMeta(), fail_first_n_chat=99)
    with RunningServer(state) as srv:
        _configure(monkeypatch, srv, meta_max_retries=3)
        with pytest.raises(MetaApiError, match="503"):
            HttpMetaClient().chat(msgs, model="m")
        assert state.chat_attempts == 3  # gave up after the retry budget
    state = MockState(FakeMeta(), api_key="other")
    with RunningServer(state) as srv:
        _configure(monkeypatch, srv)
        with pytest.raises(MetaApiError, match="401"):
            HttpMetaClient().chat(msgs, model="m")
        assert state.chat_attempts == 1  # a 401 is not retried


def test_chat_error_includes_endpoint_and_model_without_request_payload(monkeypatch):
    with RunningServer(MockState(FakeMeta())) as srv:
        _configure(monkeypatch, srv, meta_chat_path="missing")
        with pytest.raises(MetaApiError, match=r"endpoint 'missing' model 'muse-spark-1.3'"):
            HttpMetaClient().chat([{"role": "user", "content": "hi"}], model="muse-spark-1.3")


# ---- the whole agent over real HTTP --------------------------------------------------------------

@pytest.mark.parametrize("fmt", ["png", "rle_str"])
def test_full_agent_run_over_http(db, storage, monkeypatch, fmt):
    state = MockState(FakeMeta(), mask_format=fmt)
    e, n = make_pair(1)
    with RunningServer(state) as srv:
        _configure(monkeypatch, srv)
        inv = make_investigation(db, storage, e, n)
        run = new_run(db, inv)
        execute_run(db, run.id, HttpMetaClient(), storage)
    db.expire_all()
    run = db.get(AnalysisRun, run.id)
    assert run.status == "completed", run.error
    assert run.metrics["buildings"]["change_direction"] == "increase"
    assert db.query(Report).filter_by(run_id=run.id).one().kind == "full"
    assert state.wire_errors == []  # the requests we send are valid OpenAI-style chat payloads
    assert len(state.segment_requests) == 2
    # vision QC really sent two images (scene + overlay) as image_url parts
    vision = [r for r in state.chat_requests
              if any(isinstance(m.get("content"), list) for m in r["messages"])]
    assert len(vision) == 2
    parts = [p for m in vision[0]["messages"] if isinstance(m.get("content"), list) for p in m["content"]]
    assert [p["type"] for p in parts].count("image_url") == 2
    # later agent turns replay assistant tool_calls + tool results in OpenAI format
    last = state.chat_requests[-1]["messages"]
    assert any(m["role"] == "assistant" and m.get("tool_calls") for m in last)
    assert any(m["role"] == "tool" and "tool_call_id" in m for m in last)
