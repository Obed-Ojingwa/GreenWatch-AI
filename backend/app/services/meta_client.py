"""All HTTP to the Meta Model API lives here.

NOTHING in this file has been verified against the live API yet. Gate 0 (scripts/gate0.py) exists to
confirm it. The adapter is deliberately configuration-driven so that most Gate 0 findings need only
.env changes, not code changes:

  * chat: assumed OpenAI-style chat completions (messages, tools, tool_calls, response_format,
    image_url parts). Path/model/auth header are configurable.
  * segmentation: the request body is a JSON template (META_SEGMENT_BODY) and the location of the
    masks in the response is a dotted path (META_SEGMENT_MASKS_PATH); each mask is decoded by
    app.services.mask_codec, which understands PNG/base64, COCO RLE, polygons and raw rasters.

Record what you learn in docs/meta-api-notes.md.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import time  # only time.sleep is used through this name (tests replace it to skip backoff)
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Protocol

import httpx
import numpy as np
from PIL import Image

from app.core.config import Settings, get_settings
from app.core.errors import MetaApiError
from app.services.mask_codec import decode_mask

log = logging.getLogger("greenwatch.meta")


@dataclass
class ChatToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatResponse:
    content: str | None = None
    tool_calls: list[ChatToolCall] = field(default_factory=list)


class MetaClient(Protocol):
    def segment(self, image_png: bytes, prompt: str) -> list[np.ndarray]:
        """Return one bool mask (same HxW as the image) per detected instance; [] when nothing is detected."""

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        tools: list[dict[str, Any]] | None = None,
        images: list[bytes] | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> ChatResponse: ...


# ---- pure helpers (unit-tested) ----------------------------------------------------------------------

class PathNotFound(KeyError):
    """A configured response path does not exist (a wrong path must never look like 'nothing detected')."""


def extract_path(data: Any, path: str) -> list[Any]:
    """Follow a dotted path through dicts/lists. A numeric token indexes a list; `*` fans out over a list.

    Always returns a list. `"results.0.instances.*.mask"` -> every instance's mask. An empty list that
    exists returns []; a missing key or index raises PathNotFound.
    """
    nodes: list[Any] = [data]
    for tok in [t for t in path.split(".") if t != ""]:
        nxt: list[Any] = []
        for n in nodes:
            if tok == "*":
                if isinstance(n, list):
                    nxt.extend(n)
                elif isinstance(n, dict):
                    nxt.extend(n.values())
                else:
                    raise PathNotFound(f"'*' applied to {type(n).__name__} in path '{path}'")
            elif isinstance(n, list) and tok.lstrip("-").isdigit():
                i = int(tok)
                if not -len(n) <= i < len(n):
                    raise PathNotFound(f"index {i} out of range in path '{path}'")
                nxt.append(n[i])
            elif isinstance(n, dict) and tok in n:
                nxt.append(n[tok])
            else:
                raise PathNotFound(f"'{tok}' not found in path '{path}'")
        nodes = nxt
    if len(nodes) == 1 and isinstance(nodes[0], list) and not path.rstrip(".").endswith("*"):
        return list(nodes[0])  # a path that points at a list enumerates it
    return nodes


def fill_template(obj: Any, replacements: dict[str, str]) -> Any:
    """Recursively substitute placeholders inside string values of a JSON-like structure."""
    if isinstance(obj, str):
        for k, v in replacements.items():
            obj = obj.replace(k, v)
        return obj
    if isinstance(obj, list):
        return [fill_template(x, replacements) for x in obj]
    if isinstance(obj, dict):
        return {k: fill_template(v, replacements) for k, v in obj.items()}
    return obj


def clean_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop null fields: OpenAI-compatible servers reject `content: null` / `tool_calls: null`."""
    out: list[dict[str, Any]] = []
    for m in messages:
        m = {k: v for k, v in m.items() if v is not None}
        if "content" not in m and "tool_calls" not in m:
            m["content"] = ""
        out.append(m)
    return out


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# ---- HTTP client -------------------------------------------------------------------------------------

class HttpMetaClient:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()

    # transport ------------------------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        s = self.s
        value = f"{s.meta_auth_scheme} {s.meta_api_key}".strip() if s.meta_api_key else ""
        h = {"Content-Type": "application/json"}
        if value:
            h[s.meta_auth_header or "Authorization"] = value
        return h

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        s = self.s
        if not s.meta_base_url:
            raise MetaApiError("META_BASE_URL is not set; configure the Meta API (see docs/meta-api-notes.md).")
        if not s.meta_api_key:
            raise MetaApiError("META_API_KEY is not set.")
        url = s.meta_base_url.rstrip("/") + "/" + path.lstrip("/")
        attempts = max(1, int(s.meta_max_retries))
        last: MetaApiError | None = None
        with httpx.Client(timeout=s.meta_timeout_seconds) as http:
            for attempt in range(attempts):
                t0 = perf_counter()
                try:
                    resp = http.post(url, json=body, headers=self._headers())
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last = MetaApiError(f"Meta API request failed: {type(exc).__name__}: {exc}")
                    log.warning("meta transport error (attempt %s/%s): %s", attempt + 1, attempts, exc)
                else:
                    log.info("meta POST %s -> %s in %.2fs", path, resp.status_code, perf_counter() - t0)
                    if resp.status_code == 429 or resp.status_code >= 500:
                        last = MetaApiError(f"Meta API {resp.status_code}: {resp.text[:300]}", resp.status_code)
                    elif resp.status_code >= 400:
                        raise MetaApiError(f"Meta API {resp.status_code}: {resp.text[:500]}", resp.status_code)
                    else:
                        try:
                            return resp.json()
                        except ValueError as exc:
                            raise MetaApiError(f"Meta API returned a non-JSON body: {resp.text[:200]}") from exc
                if attempt < attempts - 1:
                    time.sleep(min(2**attempt, 8))
        assert last is not None
        raise last

    # segmentation ---------------------------------------------------------------------------------
    def segment(self, image_png: bytes, prompt: str) -> list[np.ndarray]:
        s = self.s
        if not (s.meta_segment_path and s.meta_segment_body and s.meta_segment_masks_path):
            raise MetaApiError(
                "The SAM segmentation contract is not configured: set META_SEGMENT_PATH, META_SEGMENT_BODY "
                "and META_SEGMENT_MASKS_PATH from the Meta docs (see docs/meta-api-notes.md)."
            )
        try:
            template = json.loads(s.meta_segment_body)
        except ValueError as exc:
            raise MetaApiError(f"META_SEGMENT_BODY is not valid JSON: {exc}") from exc
        with Image.open(io.BytesIO(image_png)) as im:
            width, height = im.size
        b64 = _b64(image_png)
        body = fill_template(template, {
            "{{model}}": s.sam_model, "{{prompt}}": prompt,
            "{{image_b64}}": b64, "{{image_data_uri}}": f"data:image/png;base64,{b64}",
        })
        data = self._post(s.meta_segment_path, body)
        try:
            raw_masks = extract_path(data, s.meta_segment_masks_path)
        except PathNotFound as exc:
            raise MetaApiError(
                f"META_SEGMENT_MASKS_PATH '{s.meta_segment_masks_path}' was not found in the response "
                f"({exc}). Top-level keys: {sorted(data) if isinstance(data, dict) else type(data).__name__}."
            ) from exc
        return [decode_mask(m, height, width) for m in raw_masks]

    # chat / agent ---------------------------------------------------------------------------------
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        tools: list[dict[str, Any]] | None = None,
        images: list[bytes] | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> ChatResponse:
        msgs = clean_messages(messages)
        if images:
            for m in reversed(msgs):
                if m.get("role") == "user":
                    text = m["content"] if isinstance(m.get("content"), str) else ""
                    m["content"] = [{"type": "text", "text": text}] + [
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + _b64(b)}} for b in images
                    ]
                    break
        body: dict[str, Any] = {"model": model, "messages": msgs}
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
        if json_schema:
            body["response_format"] = {"type": "json_schema", "json_schema": json_schema}
        return self._parse_chat(self._post(self.s.meta_chat_path, body))

    @staticmethod
    def _parse_chat(data: dict[str, Any]) -> ChatResponse:
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise MetaApiError(
                f"Unrecognised chat response; update HttpMetaClient._parse_chat. Keys: "
                f"{sorted(data) if isinstance(data, dict) else type(data).__name__}"
            ) from exc
        calls: list[ChatToolCall] = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args or "{}")
                except ValueError as exc:
                    raise MetaApiError(f"Tool call arguments are not valid JSON: {args[:200]}") from exc
            calls.append(ChatToolCall(id=str(tc.get("id") or f"call_{i}"), name=fn.get("name", ""), arguments=args or {}))
        return ChatResponse(content=msg.get("content"), tool_calls=calls)


# ---- factory (tests inject a fake) -------------------------------------------------------------------

_override: MetaClient | None = None


def get_meta_client() -> MetaClient:
    return _override if _override is not None else HttpMetaClient()


def set_meta_client(client: MetaClient | None) -> None:
    """Test hook / dev override. Pass None to restore the real client."""
    global _override
    _override = client
