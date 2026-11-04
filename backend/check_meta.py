#!/usr/bin/env python3
"""Find which Meta Model API endpoint + model id works for YOUR key, and what the app needs from it.

Run (PowerShell) from the backend folder:
    python check_meta.py

Needs only the Python standard library. It prompts for the key and never reads .env, prints, or saves
the key. It may make several billable requests.
"""
import getpass
import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("META_BASE_URL", "https://api.meta.ai/v1").rstrip("/")
PING = "Reply with exactly the word OK."
PNG_1PX = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
DATA_URL = "data:image/png;base64," + PNG_1PX
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
TOOL_PARAMS = {"type": "object", "properties": {}, "required": []}


def get_key():
    return getpass.getpass("Meta Model API key (hidden, not saved): ").strip()


def call(key, method, path, body=None):
    req = urllib.request.Request(
        BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw[:200]}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": {"message": f"{type(e).__name__}: {e}"}}


def err(data):
    e = data.get("error") if isinstance(data, dict) else None
    if isinstance(e, dict):
        return f"{e.get('code') or e.get('type')}: {str(e.get('message'))[:90]}"
    return str(e or (data.get("raw") if isinstance(data, dict) else data))[:100]


def chat_text(d):
    try:
        return (d["choices"][0]["message"].get("content") or "").strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""


def resp_text(d):
    out = []
    for item in d.get("output") or []:
        for c in (item.get("content") or []) if isinstance(item, dict) else []:
            if isinstance(c, dict) and c.get("type") == "output_text":
                out.append(c.get("text") or "")
    return "".join(out).strip()


def ok_ping(kind, status, data):
    text = chat_text(data) if kind == "chat" else resp_text(data)
    return status == 200 and bool(text), (f"reply={text[:24]!r}" if status == 200 else err(data))


def main():
    key = get_key()
    print(f"Base URL: {BASE}\n")
    st, data = call(key, "GET", "/models")
    ids = [m.get("id") for m in (data.get("data") or []) if isinstance(m, dict)] if st == 200 else []
    print(f"GET /models -> {st}" + (f" ({len(ids)} models)" if st == 200 else f": {err(data)}"))
    for i in ids:
        if i and ("muse" in i or "sam" in i):
            print("   available:", i)

    models = ["muse-spark-1.3", "muse-spark-1.3-contributor", "muse-spark-1.2", "muse-spark-1.2-contributor", "muse-spark-1.1"]
    models += [i for i in ids if i and "muse-spark" in i and i not in models]
    print("\n--- Which endpoint + model answers a plain message? ---")
    works = {"chat": [], "resp": []}
    for kind, path in (("chat", "/chat/completions"), ("resp", "/responses")):
        for m in models:
            body = {"model": m, "messages": [{"role": "user", "content": PING}]} if kind == "chat" else {"model": m, "input": PING}
            st, d = call(key, "POST", path, body)
            ok, note = ok_ping(kind, st, d)
            print(f"  {path:18} {m:28} HTTP {st}  {'OK  ' if ok else 'FAIL'} {note}")
            if ok:
                works[kind].append(m)

    print("\n--- What the app needs, on the first working model ---")
    if works["chat"]:
        m = works["chat"][0]
        st, d = call(key, "POST", "/chat/completions", {"model": m, "messages": [{"role": "user", "content": "Call the tool get_number."}],
                     "tools": [{"type": "function", "function": {"name": "get_number", "description": "Returns a number", "parameters": TOOL_PARAMS}}]})
        calls = (((d.get("choices") or [{}])[0].get("message") or {}).get("tool_calls")) if st == 200 else None
        print(f"  chat/completions tool calling      {'PASS' if calls else 'FAIL'}  {'' if calls else err(d)}")
        st, d = call(key, "POST", "/chat/completions", {"model": m, "messages": [{"role": "user", "content": 'Return JSON {"ok": true}.'}],
                     "response_format": {"type": "json_schema", "json_schema": {"name": "ping", "schema": SCHEMA, "strict": True}}})
        good = st == 200 and '"ok"' in chat_text(d)
        print(f"  chat/completions json_schema       {'PASS' if good else 'FAIL'}  {'' if good else err(d)}")
        st, d = call(key, "POST", "/chat/completions", {"model": m, "messages": [{"role": "user", "content": [
            {"type": "text", "text": "Reply OK if you can see an image."}, {"type": "image_url", "image_url": {"url": DATA_URL}},
            {"type": "image_url", "image_url": {"url": DATA_URL}}]}]})
        print(f"  chat/completions 2 images          {'PASS' if st == 200 and chat_text(d) else 'FAIL'}  {'' if st == 200 else err(d)}")
    if works["resp"]:
        m = works["resp"][0]
        st, d = call(key, "POST", "/responses", {"model": m, "input": "Call the tool get_number.",
                     "tools": [{"type": "function", "name": "get_number", "description": "Returns a number", "parameters": TOOL_PARAMS}]})
        fc = st == 200 and any(isinstance(i, dict) and i.get("type") == "function_call" for i in d.get("output") or [])
        print(f"  responses tool calling             {'PASS' if fc else 'FAIL'}  {'' if fc else err(d)}")
        st, d = call(key, "POST", "/responses", {"model": m, "input": 'Return JSON {"ok": true}.',
                     "text": {"format": {"type": "json_schema", "name": "ping", "schema": SCHEMA, "strict": True}}})
        good = st == 200 and '"ok"' in resp_text(d)
        print(f"  responses json_schema              {'PASS' if good else 'FAIL'}  {'' if good else err(d)}")

    print("\n=== RESULT ===")
    if works["chat"]:
        print(f"Set on Render (and in backend\\.env):  META_CHAT_PATH=chat/completions   AGENT_MODEL={works['chat'][0]}")
        print("Leave VISION_QC_MODEL empty. Then redeploy/restart Render and run again.")
    elif works["resp"]:
        print(f"Only /responses works for your key (model {works['resp'][0]}). The app's chat calls must switch to /responses:")
        print("send me this whole output (it contains no key) and I will patch it.")
    else:
        print("Nothing answered. Check the key's credits/permissions at dev.meta.ai, or send me this whole output.")


if __name__ == "__main__":
    main()


    