import io
import json
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from PIL import Image

from app.core.config import get_settings
from app.core.errors import MetaApiError
from app.services import sam_responses
from app.services.meta_client import HttpMetaClient

_REAL_HTTPX_CLIENT = httpx.Client


def _png(width=6, height=5):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (100, 100, 100)).save(buffer, format="PNG")
    return buffer.getvalue()


def _sse(*events):
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()


def _mock_http(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        sam_responses.httpx,
        "Client",
        lambda **kwargs: _REAL_HTTPX_CLIENT(transport=transport, **kwargs),
    )


def test_responses_body_uses_documented_message_and_mask_encoding():
    body = sam_responses.build_body(
        model="sam-3.1",
        prompt="buildings",
        image_png=_png(),
        mask_encoding="one_bit",
    )
    assert body["model"] == "sam-3.1"
    assert body["stream"] is True
    assert body["metadata"] == {"mask_encoding": "one_bit"}
    assert [part["type"] for part in body["input"][0]["content"]] == ["input_text", "input_image"]
    assert body["input"][0]["content"][0]["text"] == "buildings"
    assert body["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,")


def test_responses_segment_works_without_json_template_or_masks_path(monkeypatch):
    settings = get_settings()
    for name, value in {
        "meta_base_url": "https://api.example.test/v1",
        "meta_api_key": "test-key",
        "meta_auth_header": "Authorization",
        "meta_auth_scheme": "Bearer",
        "meta_segment_path": "responses",
        "meta_segment_body": "",
        "meta_segment_masks_path": "",
        "meta_sam_mask_encoding": "one_bit",
        "meta_max_retries": 1,
    }.items():
        monkeypatch.setattr(settings, name, value)

    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/v1/responses"
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["stream"] is True
        assert body["metadata"]["mask_encoding"] == "one_bit"
        assert body["input"][0]["content"][0]["text"] == "buildings"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(
                {"type": "response.output_text.delta", "delta": ""},
                {"type": "response.completed", "response": {"status": "completed", "output": []}},
            ),
        )

    _mock_http(monkeypatch, handler)
    masks = HttpMetaClient(settings).segment(_png(), "buildings")
    assert masks == []


def test_responses_stream_must_complete_and_be_valid_json(monkeypatch):
    _mock_http(
        monkeypatch,
        lambda _: httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'data: {"type":"response.output_text.delta","delta":""}\n\n',
        ),
    )
    with pytest.raises(MetaApiError, match="did not complete"):
        sam_responses.request(
            base_url="https://api.example.test/v1",
            path="responses",
            headers={},
            timeout=10,
            retries=1,
            body={},
        )

    _mock_http(
        monkeypatch,
        lambda _: httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"data: not-json\n\n",
        ),
    )
    with pytest.raises(MetaApiError, match="malformed SSE JSON"):
        sam_responses.request(
            base_url="https://api.example.test/v1",
            path="responses",
            headers={},
            timeout=10,
            retries=1,
            body={},
        )


def test_responses_http_errors_include_endpoint_and_model(monkeypatch):
    _mock_http(monkeypatch, lambda _: httpx.Response(404))
    with pytest.raises(MetaApiError, match=r"endpoint 'responses' model 'sam-3.1'"):
        sam_responses.request(
            base_url="https://api.example.test/v1",
            path="responses",
            headers={},
            timeout=10,
            retries=1,
            body={"model": "sam-3.1"},
        )


def test_sam_mask_is_placed_in_frame_and_only_latest_revision_is_used(monkeypatch):
    mask_text = "<0f>0<|mask;x=2;y=1;data=2,2,!!!!|>"
    earlier = SimpleNamespace(
        kind="mask",
        identity="same-object",
        revision=0,
        order=0,
        mask=SimpleNamespace(width=2, height=2, payload="old"),
        bounds=SimpleNamespace(left=2, top=1, right=4, bottom=3),
    )
    latest = SimpleNamespace(
        kind="mask",
        identity="same-object",
        revision=1,
        order=1,
        mask=SimpleNamespace(width=2, height=2, payload="new"),
        bounds=SimpleNamespace(left=2, top=1, right=4, bottom=3),
    )

    class Parser:
        def push(self, _):
            return ()

        def finish(self, _):
            return SimpleNamespace(
                result=SimpleNamespace(records=(earlier, latest), diagnostics=())
            )

    monkeypatch.setattr(
        sam_responses,
        "image_segmentation_format",
        lambda: SimpleNamespace(create_parser=lambda: Parser()),
    )
    decoded = []

    def decode(mask):
        decoded.append(mask.payload)
        return bytes((0, 1, 1, 0))

    monkeypatch.setattr(sam_responses, "decode_mask_to_raster", decode)
    masks = sam_responses.parse_masks(mask_text, width=6, height=5)

    assert decoded == ["new"]
    assert len(masks) == 1
    expected = np.zeros((5, 6), dtype=bool)
    expected[1:3, 2:4] = [[False, True], [True, False]]
    np.testing.assert_array_equal(masks[0], expected)


def test_empty_sam_output_is_no_detections_but_unrecognized_text_fails():
    assert sam_responses.parse_masks("", width=6, height=5) == []
    with pytest.raises(MetaApiError, match="unrecognized text"):
        sam_responses.parse_masks("no segmentation returned", width=6, height=5)


def test_documented_sam_record_decodes_with_public_parser():
    # Protocol example adapted to the image-required frame-zero header:
    # https://github.com/meta-models/meta-sam/blob/main/protocol/sam3.md
    text = (
        "<0f>0<|box;x1=10;y1=20;x2=14;y2=24;w=100;h=80|>"
        "<|mask;x=0;y=0;data=5,5,!!!!!(QO(0lu8?|>"
    )
    masks = sam_responses.parse_masks(text, width=100, height=80)
    assert len(masks) == 1
    assert masks[0].shape == (80, 100)
    assert int(masks[0].sum()) == 10
