"""Adapter for SAM's Responses API text stream and spatial mask records."""
from __future__ import annotations

import base64
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlsplit

import httpx
import numpy as np
from PIL import Image
from meta_sam_parser import (
    CompletedOutcome,
    ResponsesStreamError,
    SegmentationMaskIdentity,
    SegmentationMaskRecord,
    decode_mask_to_raster,
    image_segmentation_format,
)

from app.core.errors import MetaApiError

log = logging.getLogger("greenwatch.meta")
MAX_RESPONSE_CHARS = 32 * 1024 * 1024
_MASK_DIMS = re.compile(r"<\|mask(?:;[^|]*)?data=(\d+),(\d+),")


@dataclass(frozen=True)
class SamResponse:
    text: str
    completed: bool


def is_responses_path(path: str) -> bool:
    endpoint = urlsplit(path).path.rstrip("/")
    return endpoint == "responses" or endpoint.endswith("/responses")


def _url(base_url: str, path: str) -> str:
    if urlsplit(path).scheme:
        return path
    base = base_url.rstrip("/")
    suffix = path.lstrip("/")
    if urlsplit(base).path.rstrip("/").endswith("/v1") and suffix.startswith("v1/"):
        suffix = suffix[3:]
    return f"{base}/{suffix}"


def _event_text(payload: dict[str, Any], streamed: str) -> str:
    event_type = payload.get("type")
    if event_type == "response.output_text.delta":
        delta = payload.get("delta")
        if not isinstance(delta, str):
            raise MetaApiError("SAM Responses API emitted an invalid text delta.")
        if len(streamed) + len(delta) > MAX_RESPONSE_CHARS:
            raise MetaApiError("SAM Responses API output exceeded the configured safety limit.")
        return streamed + delta
    return streamed


def _output_text(output: Any) -> str:
    if not isinstance(output, list):
        raise MetaApiError("SAM Responses API completion did not contain an output list.")
    parts = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            raise MetaApiError("SAM Responses API completion contained an invalid output item.")
        content = item.get("content")
        if not isinstance(content, list) or not content:
            raise MetaApiError("SAM Responses API completion contained invalid output content.")
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "output_text":
                raise MetaApiError("SAM Responses API returned unsupported non-text output.")
            value = part.get("text")
            if not isinstance(value, str):
                raise MetaApiError("SAM Responses API completion contained invalid output text.")
            parts.append(value)
    return "".join(parts)


def _read_sse(lines: Iterable[str]) -> SamResponse:
    text = ""
    final_text: str | None = None
    data_lines: list[str] = []
    completed = False
    incomplete = False

    def dispatch() -> None:
        nonlocal text, final_text, completed, incomplete
        if not data_lines:
            return
        data = "\n".join(data_lines)
        data_lines.clear()
        if data == "[DONE]":
            return
        try:
            payload = json.loads(data)
        except ValueError as exc:
            raise MetaApiError("SAM Responses API emitted malformed SSE JSON.") from exc
        if not isinstance(payload, dict):
            raise MetaApiError("SAM Responses API emitted an invalid SSE event.")
        event_type = payload.get("type")
        text = _event_text(payload, text)
        if event_type == "response.output_text.done":
            value = payload.get("text")
            if isinstance(value, str):
                final_text = value
        elif event_type == "response.content_part.done":
            part = payload.get("part", payload)
            if isinstance(part, dict) and part.get("type") == "output_text":
                value = part.get("text")
                if isinstance(value, str):
                    final_text = value
        elif event_type == "response.completed":
            response = payload.get("response")
            if not isinstance(response, dict) or response.get("status") != "completed":
                raise MetaApiError("SAM Responses API completion event was invalid.")
            completed_text = _output_text(response.get("output"))
            if text and completed_text != text:
                raise MetaApiError("SAM Responses API text stream did not match its completion event.")
            if final_text is not None and final_text != completed_text:
                raise MetaApiError("SAM Responses API text stream did not match its completion event.")
            if not text:
                final_text = completed_text
            completed = True
        elif event_type in {"response.incomplete", "response.failed", "error"}:
            incomplete = True

    for line in lines:
        if line == "":
            dispatch()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        if sum(map(len, data_lines)) > MAX_RESPONSE_CHARS:
            raise MetaApiError("SAM Responses API output exceeded the configured safety limit.")
    dispatch()
    if incomplete or not completed:
        raise MetaApiError("SAM Responses API stream did not complete successfully.")
    if final_text is not None:
        if text and final_text != text:
            raise MetaApiError("SAM Responses API text stream did not match its completion event.")
        text = final_text
    if len(text) > MAX_RESPONSE_CHARS:
        raise MetaApiError("SAM Responses API output exceeded the configured safety limit.")
    return SamResponse(text=text, completed=True)


def _read_json(response: httpx.Response) -> SamResponse:
    try:
        payload = response.json()
    except ValueError as exc:
        raise MetaApiError("SAM Responses API returned neither a valid event stream nor JSON.") from exc
    if not isinstance(payload, dict):
        raise MetaApiError("SAM Responses API returned an invalid response.")
    if payload.get("status") != "completed":
        raise MetaApiError("SAM Responses API response was not completed.")
    output = payload.get("output")
    if not isinstance(output, list):
        raise MetaApiError("SAM Responses API response did not contain output.")
    text = _output_text(output)
    if len(text) > MAX_RESPONSE_CHARS:
        raise MetaApiError("SAM Responses API output exceeded the configured safety limit.")
    return SamResponse(text=text, completed=True)


def request(
    *,
    base_url: str,
    path: str,
    headers: dict[str, str],
    timeout: float,
    retries: int,
    body: dict[str, Any],
) -> SamResponse:
    attempts = max(1, int(retries))
    last_error: MetaApiError | None = None
    url = _url(base_url, path)
    endpoint = urlsplit(path).path.rstrip("/") or path.split("?", 1)[0].rstrip("/")
    model = str(body.get("model") or "<unset>")
    request_context = f"endpoint '{endpoint}' model '{model}'"
    with httpx.Client(timeout=timeout) as client:
        for attempt in range(attempts):
            started = time.perf_counter()
            try:
                with client.stream("POST", url, json=body, headers=headers) as response:
                    log.info(
                        "SAM Responses POST endpoint=%s model=%s -> %s in %.2fs",
                        endpoint, model, response.status_code, time.perf_counter() - started,
                    )
                    if response.status_code == 429 or response.status_code >= 500:
                        last_error = MetaApiError(
                            f"SAM Responses API returned HTTP {response.status_code} for {request_context}.",
                            response.status_code,
                        )
                    elif response.status_code >= 300:
                        raise MetaApiError(
                            f"SAM Responses API returned HTTP {response.status_code} for {request_context}.",
                            response.status_code,
                        )
                    elif "text/event-stream" in response.headers.get("content-type", "").lower():
                        return _read_sse(response.iter_lines())
                    else:
                        return _read_json(response)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = MetaApiError(
                    f"SAM Responses API request failed for {request_context}: {type(exc).__name__}."
                )
                log.warning("SAM Responses transport error (attempt %s/%s): %s", attempt + 1, attempts, type(exc).__name__)
            if attempt < attempts - 1:
                time.sleep(min(2**attempt, 8))
    if last_error is None:
        raise MetaApiError("SAM Responses API request failed.")
    raise last_error


def build_body(
    *,
    model: str,
    prompt: str,
    image_png: bytes,
    mask_encoding: str,
) -> dict[str, Any]:
    if mask_encoding and mask_encoding not in {"one_bit", "lossless"}:
        raise MetaApiError("META_SAM_MASK_ENCODING must be 'one_bit', 'lossless', or empty.")
    data_uri = "data:image/png;base64," + base64.b64encode(image_png).decode("ascii")
    body: dict[str, Any] = {
        "model": model,
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": data_uri},
            ],
        }],
        "stream": True,
    }
    if mask_encoding:
        body["metadata"] = {"mask_encoding": mask_encoding}
    return body


def parse_masks(text: str, width: int, height: int) -> list[np.ndarray]:
    if not text:
        return []
    for match in _MASK_DIMS.finditer(text):
        mask_width, mask_height = int(match.group(1)), int(match.group(2))
        if mask_width > width or mask_height > height:
            raise MetaApiError("SAM mask dimensions exceed the input image.")
    try:
        parser = image_segmentation_format().create_parser()
        parser.push(text)
        result = parser.finish(CompletedOutcome()).result
    except ResponsesStreamError as exc:
        raise MetaApiError("SAM output could not be decoded as segmentation records.") from exc
    if any(diagnostic.severity == "error" for diagnostic in result.diagnostics):
        raise MetaApiError("SAM output contained an invalid segmentation record.")
    if any(getattr(record, "kind", None) == "text" and record.text.strip() for record in result.records):
        raise MetaApiError("SAM output contained unrecognized text instead of segmentation records.")

    latest: dict[SegmentationMaskIdentity, SegmentationMaskRecord] = {}
    for record in result.records:
        if record.kind == "mask":
            previous = latest.get(record.identity)
            if previous is None or record.revision > previous.revision:
                latest[record.identity] = record

    masks: list[np.ndarray] = []
    for record in sorted(latest.values(), key=lambda item: item.order):
        bounds = record.bounds
        if not all(math.isfinite(value) for value in (bounds.left, bounds.top, bounds.right, bounds.bottom)):
            raise MetaApiError("SAM returned non-finite mask bounds.")
        left, top = math.floor(bounds.left), math.floor(bounds.top)
        right, bottom = math.ceil(bounds.right), math.ceil(bounds.bottom)
        if left < 0 or top < 0 or right > width or bottom > height or right <= left or bottom <= top:
            raise MetaApiError("SAM returned mask bounds outside the input image.")
        if record.mask.width > width or record.mask.height > height:
            raise MetaApiError("SAM mask dimensions exceed the input image.")
        raster = decode_mask_to_raster(record.mask)
        local = np.frombuffer(raster, dtype=np.uint8).reshape((record.mask.height, record.mask.width)) > 0
        target_size = (right - left, bottom - top)
        if local.shape[::-1] != target_size:
            local = np.asarray(
                Image.fromarray(local.astype(np.uint8) * 255).resize(target_size, Image.Resampling.NEAREST)
            ) > 0
        full = np.zeros((height, width), dtype=bool)
        full[top:bottom, left:right] = local
        masks.append(full)
    return masks
