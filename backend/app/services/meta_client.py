"""Provider clients for Gemini chat/vision and Meta SAM segmentation.

Gemini uses the OpenAI-compatible Chat Completions API for agent/tool calls and vision QC. Meta SAM
uses the Responses API stream when META_SEGMENT_PATH ends in ``responses``; other paths retain the
configurable JSON-mask adapter. Provider access still requires a live check with the configured keys.
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
from urllib.parse import urlsplit

import httpx
import numpy as np
from PIL import Image

from app.core.config import Settings, get_settings
from app.core.errors import MetaApiError
from app.services.mask_codec import decode_mask
from app.services import sam_responses

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


@dataclass(frozen=True)
class ModelCatalog:
    status: int | None
    model_ids: tuple[str, ...] = ()
    error_code: str | None = None


class MetaClient(Protocol):
    def list_models(self) -> ModelCatalog: ...

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


def _provider_error_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    if isinstance(code, str):
        return code
    status = error.get("status") or error.get("type")
    if isinstance(status, str):
        return status
    return str(code) if isinstance(code, int) else None


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

    def list_models(self) -> ModelCatalog:
        s = self.s
        if not s.meta_base_url or not s.meta_api_key:
            return ModelCatalog(
                status=None,
                error_code="missing_configuration",
            )
        url = s.meta_base_url.rstrip("/") + "/models"
        try:
            with httpx.Client(timeout=s.meta_timeout_seconds) as http:
                response = http.get(url, headers=self._headers())
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            return ModelCatalog(
                status=None,
                error_code=type(exc).__name__,
            )
        if response.status_code >= 400:
            return ModelCatalog(
                status=response.status_code,
                error_code=_provider_error_code(response),
            )
        try:
            payload = response.json()
        except ValueError:
            return ModelCatalog(
                status=response.status_code,
                error_code="invalid_response",
            )
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return ModelCatalog(
                status=response.status_code,
                error_code="invalid_response",
            )
        model_ids = tuple(
            row["id"] for row in rows
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        )
        return ModelCatalog(status=response.status_code, model_ids=model_ids)

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        s = self.s
        if not s.meta_base_url:
            raise MetaApiError("META_BASE_URL is not set; configure the Meta API (see docs/meta-api-notes.md).")
        if not s.meta_api_key:
            raise MetaApiError("META_API_KEY is not set.")
        url = s.meta_base_url.rstrip("/") + "/" + path.lstrip("/")
        endpoint = path.split("?", 1)[0].rstrip("/")
        model = str(body.get("model") or "<unset>")
        request_context = f"endpoint '{endpoint}' model '{model}'"
        attempts = max(1, int(s.meta_max_retries))
        last: MetaApiError | None = None
        with httpx.Client(timeout=s.meta_timeout_seconds) as http:
            for attempt in range(attempts):
                t0 = perf_counter()
                try:
                    resp = http.post(url, json=body, headers=self._headers())
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last = MetaApiError(f"Meta API request failed for {request_context}: {type(exc).__name__}: {exc}")
                    log.warning("meta transport error (attempt %s/%s): %s", attempt + 1, attempts, exc)
                else:
                    log.info(
                        "meta POST endpoint=%s model=%s -> %s in %.2fs",
                        endpoint, model, resp.status_code, perf_counter() - t0,
                    )
                    if resp.status_code == 429 or resp.status_code >= 500:
                        last = MetaApiError(
                            f"Meta API {resp.status_code} for {request_context}: {resp.text[:300]}",
                            resp.status_code,
                            _provider_error_code(resp),
                        )
                    elif resp.status_code >= 400:
                        raise MetaApiError(
                            f"Meta API {resp.status_code} for {request_context}: {resp.text[:500]}",
                            resp.status_code,
                            _provider_error_code(resp),
                        )
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
        if not s.meta_segment_path:
            raise MetaApiError(
                "The SAM segmentation contract is not configured: set META_SEGMENT_PATH "
                "(see docs/meta-api-notes.md)."
            )
        with Image.open(io.BytesIO(image_png)) as im:
            width, height = im.size
        if sam_responses.is_responses_path(s.meta_segment_path):
            if not s.meta_base_url:
                raise MetaApiError("META_BASE_URL is not set; configure the Meta API (see docs/meta-api-notes.md).")
            if not s.meta_api_key:
                raise MetaApiError("META_API_KEY is not set.")
            body = sam_responses.build_body(
                model=s.sam_model,
                prompt=prompt,
                image_png=image_png,
                mask_encoding=s.meta_sam_mask_encoding,
            )
            reply = sam_responses.request(
                base_url=s.meta_base_url,
                path=s.meta_segment_path,
                headers=self._headers(),
                timeout=s.meta_timeout_seconds,
                retries=s.meta_max_retries,
                body=body,
            )
            if not reply.completed:
                raise MetaApiError("SAM Responses API stream did not complete successfully.")
            return sam_responses.parse_masks(reply.text, width, height)

        if not (s.meta_segment_body and s.meta_segment_masks_path):
            raise MetaApiError(
                "The configured JSON SAM contract requires META_SEGMENT_BODY and "
                "META_SEGMENT_MASKS_PATH (see docs/meta-api-notes.md)."
            )
        try:
            template = json.loads(s.meta_segment_body)
        except ValueError as exc:
            raise MetaApiError(f"META_SEGMENT_BODY is not valid JSON: {exc}") from exc
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
                "Unrecognised OpenAI-compatible chat response. Keys: "
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


class HttpGeminiChatClient:
    """Gemini OpenAI-compatible chat adapter; SAM remains on the Meta client."""

    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()

    def list_models(self) -> ModelCatalog:
        # The OpenAI-compatibility docs do not define a model-catalog endpoint.
        return ModelCatalog(status=None, error_code="catalog_not_supported")

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        tools: list[dict[str, Any]] | None = None,
        images: list[bytes] | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> ChatResponse:
        s = self.s
        if not s.gemini_api_key:
            raise MetaApiError("GEMINI_API_KEY is not set.")
        if not s.gemini_base_url:
            raise MetaApiError("GEMINI_BASE_URL is not set.")
        url = s.gemini_base_url.rstrip("/") + "/chat/completions"
        endpoint = urlsplit(url).path.strip("/")
        request_context = f"endpoint '{endpoint}' model '{model}'"
        headers = {
            "Authorization": f"Bearer {s.gemini_api_key}",
            "Content-Type": "application/json",
        }
        msgs = clean_messages(messages)
        if images:
            for message in reversed(msgs):
                if message.get("role") == "user":
                    text = message["content"] if isinstance(message.get("content"), str) else ""
                    message["content"] = [{"type": "text", "text": text}] + [
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + _b64(image)}}
                        for image in images
                    ]
                    break
        body: dict[str, Any] = {"model": model, "messages": msgs}
        if tools:
            body["tools"] = [{"type": "function", "function": tool} for tool in tools]
        # The OpenAI-compatible Gemini docs do not document response_format/json_schema.
        # QC still validates the model's JSON response locally and fails closed if malformed.
        del json_schema

        attempts = max(1, int(s.meta_max_retries))
        last: MetaApiError | None = None
        with httpx.Client(timeout=s.meta_timeout_seconds) as http:
            for attempt in range(attempts):
                t0 = perf_counter()
                try:
                    response = http.post(url, json=body, headers=headers)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last = MetaApiError(
                        f"Gemini API request failed for {request_context}: {type(exc).__name__}"
                    )
                    log.warning("Gemini transport error (attempt %s/%s): %s", attempt + 1, attempts, type(exc).__name__)
                else:
                    log.info(
                        "Gemini POST endpoint=%s model=%s -> %s in %.2fs",
                        endpoint, model, response.status_code, perf_counter() - t0,
                    )
                    if response.status_code == 429 or response.status_code >= 500:
                        provider_code = _provider_error_code(response)
                        log.warning(
                            "gemini request failed endpoint=%s model=%s status=%s provider_code=%s attempt=%s/%s",
                            endpoint, model, response.status_code, provider_code, attempt + 1, attempts,
                        )
                        code_detail = f" (provider code {provider_code})" if provider_code else ""
                        last = MetaApiError(
                            f"Gemini API {response.status_code}{code_detail} for {request_context}",
                            response.status_code,
                            provider_code,
                        )
                    elif response.status_code >= 400:
                        provider_code = _provider_error_code(response)
                        code_detail = f" (provider code {provider_code})" if provider_code else ""
                        raise MetaApiError(
                            f"Gemini API {response.status_code}{code_detail} for {request_context}",
                            response.status_code,
                            provider_code,
                        )
                    else:
                        try:
                            return HttpMetaClient._parse_chat(response.json())
                        except ValueError as exc:
                            raise MetaApiError("Gemini API returned a non-JSON response.") from exc
                if attempt < attempts - 1:
                    time.sleep(min(2**attempt, 60))
        assert last is not None
        raise last


class GeminiWithMetaSegmentationClient:
    """Route chat and vision to Gemini while retaining Meta SAM for pixel masks."""

    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()
        self._chat_client = HttpGeminiChatClient(self.s)
        self._segmentation_client = HttpMetaClient(self.s)

    def list_models(self) -> ModelCatalog:
        return self._chat_client.list_models()

    def segment(self, image_png: bytes, prompt: str) -> list[np.ndarray]:
        return self._segmentation_client.segment(image_png, prompt)

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        tools: list[dict[str, Any]] | None = None,
        images: list[bytes] | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> ChatResponse:
        return self._chat_client.chat(
            messages, model=model, tools=tools, images=images, json_schema=json_schema
        )


# ---- factory (tests inject a fake) -------------------------------------------------------------------

_override: MetaClient | None = None


def get_meta_client() -> MetaClient:
    if _override is not None:
        return _override
    settings = get_settings()
    if settings.ai_provider.strip().lower() == "gemini":
        return GeminiWithMetaSegmentationClient(settings)
    return HttpMetaClient(settings)


def set_meta_client(client: MetaClient | None) -> None:
    """Test hook / dev override. Pass None to restore the real client."""
    global _override
    _override = client
