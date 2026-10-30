"""TEST-ONLY HTTP server that speaks the *assumed* Meta wire format (OpenAI-style chat completions +
a JSON segmentation endpoint) and delegates the "intelligence" to tests.helpers.FakeMeta.

Purpose: exercise HttpMetaClient over a real socket (auth header, retries, request/response
serialisation, tool calls, image parts) without spending credits. It proves OUR adapter is
self-consistent; it proves nothing about Meta's real API. Never import this from application code.
"""
from __future__ import annotations

import base64
import io
import json
import socket
import threading
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from PIL import Image

from app.services.mask_codec import counts_to_rle_string, mask_to_counts


class MockState:
    def __init__(self, fake, mask_format="png", api_key="k", fail_first_n_chat=0, auth_header="Authorization",
                 auth_scheme="Bearer"):
        self.fake = fake
        self.mask_format = mask_format
        self.api_key = api_key
        self.fail_first_n_chat = fail_first_n_chat
        self.auth_header, self.auth_scheme = auth_header, auth_scheme
        self.chat_requests: list[dict] = []
        self.segment_requests: list[dict] = []
        self.chat_attempts = 0
        self.wire_errors: list[str] = []


def build_app(state: MockState) -> FastAPI:
    app = FastAPI()

    def authorised(req: Request) -> bool:
        expect = f"{state.auth_scheme} {state.api_key}".strip()
        return req.headers.get(state.auth_header) == expect

    @app.post("/v1/chat/completions")
    async def chat(req: Request):
        state.chat_attempts += 1
        if not authorised(req):
            return JSONResponse({"error": "unauthorised"}, status_code=401)
        if state.chat_attempts <= state.fail_first_n_chat:
            return JSONResponse({"error": "overloaded"}, status_code=503)
        body = await req.json()
        state.chat_requests.append(body)
        # Be as strict as an OpenAI-compatible server: no null fields, tools in function format.
        for m in body["messages"]:
            if any(v is None for v in m.values()):
                state.wire_errors.append(f"null field in message: {m}")
            if "content" not in m and "tool_calls" not in m:
                state.wire_errors.append(f"message without content or tool_calls: {m}")
        for t in body.get("tools") or []:
            if t.get("type") != "function" or not {"name", "parameters"} <= set(t.get("function", {})):
                state.wire_errors.append(f"bad tool shape: {str(t)[:120]}")
        flat, images = [], []
        for m in body["messages"]:
            c = m.get("content")
            if isinstance(c, list):
                text = "".join(p["text"] for p in c if p["type"] == "text")
                images += [base64.b64decode(p["image_url"]["url"].split(",", 1)[1]) for p in c if p["type"] == "image_url"]
                m = {**m, "content": text}
            flat.append(m)
        resp = state.fake.chat(flat, model=body["model"], tools=body.get("tools"), images=images or None,
                               json_schema=(body.get("response_format") or {}).get("json_schema"))
        msg: dict = {"role": "assistant", "content": resp.content}
        if resp.tool_calls:
            msg["tool_calls"] = [{"id": tc.id, "type": "function",
                                  "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                                 for tc in resp.tool_calls]
        return {"choices": [{"index": 0, "message": msg}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    @app.post("/v1/segment")
    async def segment(req: Request):
        if not authorised(req):
            return JSONResponse({"error": "unauthorised"}, status_code=401)
        body = await req.json()
        state.segment_requests.append(body)
        png = base64.b64decode(body["image"])
        masks = state.fake.segment(png, body["prompt"])
        out = []
        for m in masks:
            if state.mask_format == "png":
                buf = io.BytesIO()
                Image.fromarray(m.astype("uint8") * 255, mode="L").save(buf, format="PNG")
                out.append(base64.b64encode(buf.getvalue()).decode())
            elif state.mask_format == "rle_str":
                out.append({"size": list(m.shape), "counts": counts_to_rle_string(mask_to_counts(m))})
            elif state.mask_format == "wrapped":
                buf = io.BytesIO()
                Image.fromarray(m.astype("uint8") * 255, mode="L").save(buf, format="PNG")
                out.append({"score": 0.93, "mask": base64.b64encode(buf.getvalue()).decode()})
        return {"model": body["model"], "masks": out}

    return app


class RunningServer:
    def __init__(self, state: MockState):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self.server = uvicorn.Server(uvicorn.Config(build_app(state), host="127.0.0.1", port=self.port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("mock Meta server did not start")

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=5)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"
