#!/usr/bin/env python3
"""GATE 0: verify the configured chat/vision provider and Meta SAM on YOUR imagery.

Run from the backend folder so backend/.env is picked up:

    cd backend
    python ../scripts/gate0.py probe --image /path/to/earlier.jpg          # sanitized SAM mask summary
    python ../scripts/gate0.py run   --earlier a.jpg --newer b.jpg         # the full Gate 0 checklist

`probe` posts ONE real request using your META_SEGMENT_* settings. For the Responses API it writes
only sanitized mask counts and dimensions; for the legacy JSON adapter it inspects candidate mask paths.
Use it first.

`run` checks, in order, and exits 0 only if every check passes:
    config            required settings are present
    chat              one plain chat call works (auth, base URL, model id)
    json_response      a requested JSON answer parses (not a constrained-output guarantee)
    sam               SAM returns decodable masks (saved as PNGs, with pixel counts)
    overlay           an overlay image is produced
    vision_review     configured AI reviews image + overlay and returns a parseable verdict
    tool_call         a real tool-call round trip completes through the configured AI
    alignment         (only with --newer) the pair passes the app's own validation

Nothing is mocked. The script keeps going after failures so one run shows everything that is broken.
Outputs go to gate0_out/ (gitignored); inspect details locally and share only sanitized results.
Record non-secret findings in docs/meta-api-notes.md.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"


ORIG_CWD = Path.cwd()  # user-supplied relative paths are relative to where they ran the script


def _abs(p: str | None) -> Path | None:
    if not p:
        return None
    q = Path(p).expanduser()
    return (q if q.is_absolute() else ORIG_CWD / q).resolve()


# Settings read backend/.env relative to cwd, so always run from the backend folder.
_ARGS_PRE = sys.argv[1:]
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.errors import MetaApiError  # noqa: E402
from app.services.mask_codec import decode_mask  # noqa: E402
from app.services.meta_client import HttpMetaClient, extract_path, fill_template, get_meta_client  # noqa: E402
from app.services import sam_responses  # noqa: E402
from app.services.quality import mask_stats, vision_review  # noqa: E402
from app.services.segmentation import make_overlay  # noqa: E402

OUT = REPO / "gate0_out"


# ------------------------------------------------------------------ helpers
def load_png(path: Path, max_dim: int) -> tuple[bytes, np.ndarray]:
    img = Image.open(path).convert("RGB")
    if max(img.size) > max_dim:
        s = max_dim / max(img.size)
        img = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), np.asarray(img)


def png_of(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def banner(t: str) -> None:
    print(f"\n=== {t} " + "=" * max(3, 70 - len(t)))


class Checks:
    def __init__(self) -> None:
        self.results: dict[str, dict[str, Any]] = {}

    def record(self, name: str, ok: bool, detail: str = "", **extra: Any) -> bool:
        self.results[name] = {"ok": ok, "detail": detail, **extra}
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
        return ok

    def guard(self, name: str, fn) -> Any:
        """Run a check; never swallow the traceback."""
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.record(name, False, f"{type(exc).__name__}: {exc}")
            return None

    @property
    def all_ok(self) -> bool:
        return bool(self.results) and all(r["ok"] for r in self.results.values())


def segment_body(s, png: bytes, prompt: str) -> dict:
    import base64

    template = json.loads(s.meta_segment_body)
    b64 = base64.b64encode(png).decode("ascii")
    return fill_template(template, {
        "{{model}}": s.sam_model, "{{prompt}}": prompt,
        "{{image_b64}}": b64, "{{image_data_uri}}": f"data:image/png;base64,{b64}",
    })


# ------------------------------------------------------------------ probe
def describe(node: Any, indent: int = 0, key: str = "<root>", depth: int = 0) -> None:
    pad = "  " * indent
    if isinstance(node, dict):
        print(f"{pad}{key}: object ({len(node)} keys)")
        if depth < 6:
            for k, v in list(node.items())[:25]:
                describe(v, indent + 1, str(k), depth + 1)
    elif isinstance(node, list):
        print(f"{pad}{key}: list[{len(node)}]")
        if node and depth < 6:
            describe(node[0], indent + 1, "[0]", depth + 1)
    elif isinstance(node, str):
        shown = node if len(node) <= 60 else node[:40] + f"...({len(node)} chars)"
        print(f"{pad}{key}: str = {shown!r}")
    else:
        print(f"{pad}{key}: {type(node).__name__} = {node!r}")


def find_candidates(node: Any, path: list[str], out: list[tuple[str, str]]) -> None:
    """Locate things that look like masks. List elements are written as '*' so the result can be used
    directly as META_SEGMENT_MASKS_PATH."""
    if isinstance(node, dict):
        keys = set(node)
        if {"counts", "size"} <= keys:
            out.append((".".join(path), "COCO-style RLE object"))
        elif keys & {"polygon", "polygons", "segmentation", "contour", "points"}:
            out.append((".".join(path), "polygon-like object"))
        for k, v in node.items():
            find_candidates(v, path + [str(k)], out)
    elif isinstance(node, list):
        if len(node) > 50 and all(isinstance(x, (int, float)) for x in node[:20]):
            out.append((".".join(path), "long numeric array (raw raster / flat polygon?)"))
        if node:
            find_candidates(node[0], path + ["*"], out)
    elif isinstance(node, str) and len(node) > 200:
        out.append((".".join(path), f"long string ({len(node)} chars; base64 image / RLE string?)"))


def cmd_probe(args: argparse.Namespace) -> int:
    s = get_settings()
    problems = [n for n, v in (("META_API_KEY", s.meta_api_key), ("META_BASE_URL", s.meta_base_url),
                               ("META_SEGMENT_PATH", s.meta_segment_path), ("SAM_MODEL", s.sam_model)) if not v]
    if problems:
        print("Cannot probe yet; set these in backend/.env first:", ", ".join(problems))
        return 2
    OUT.mkdir(exist_ok=True)
    png, _ = load_png(_abs(args.image), s.max_image_dim)
    client = HttpMetaClient(s)
    if sam_responses.is_responses_path(s.meta_segment_path):
        t0 = time.perf_counter()
        masks = client.segment(png, args.prompt)
        dt = time.perf_counter() - t0
        summary = {
            "endpoint_mode": "responses-stream",
            "prompt": args.prompt,
            "mask_count": len(masks),
            "masks": [
                {"width": int(mask.shape[1]), "height": int(mask.shape[0]), "pixels": int(np.count_nonzero(mask))}
                for mask in masks
            ],
        }
        (OUT / "probe_summary.json").write_text(json.dumps(summary, indent=2))
        banner(f"sanitized SAM response ({dt:.2f}s) -> gate0_out/probe_summary.json")
        print(json.dumps(summary, indent=2))
        return 0
    body = segment_body(s, png, args.prompt)
    t0 = time.perf_counter()
    data = client._post(s.meta_segment_path, body)  # noqa: SLF001  (raw on purpose)
    dt = time.perf_counter() - t0
    (OUT / "probe_raw.json").write_text(json.dumps(data)[:20_000_000])
    banner(f"raw SAM response ({dt:.2f}s) -> gate0_out/probe_raw.json")
    describe(data)
    cands: list[tuple[str, str]] = []
    find_candidates(data, [], cands)
    banner("candidate META_SEGMENT_MASKS_PATH values")
    if not cands:
        print("None found. Read the structure above and the Meta docs, then set META_SEGMENT_MASKS_PATH by hand.")
        return 1
    h, w = Image.open(io.BytesIO(png)).size[::-1]
    for p, why in cands:
        try:
            items = extract_path(data, p)
            m = decode_mask(items[0], h, w) if items else None
            verdict = "decodes OK, first mask has %d px" % int(np.count_nonzero(m)) if m is not None else "path ok but empty list"
        except Exception as exc:  # noqa: BLE001
            verdict = f"does NOT decode: {type(exc).__name__}: {str(exc)[:120]}"
        print(f"  META_SEGMENT_MASKS_PATH={p}\n      ({why}) -> {verdict}")
    return 0


# ------------------------------------------------------------------ run
def cmd_run(args: argparse.Namespace) -> int:
    s = get_settings()
    OUT.mkdir(exist_ok=True)
    ck = Checks()
    prompts = [p.strip() for p in args.prompts.split(",") if p.strip()]
    vision_model = s.vision_qc_model or s.agent_model

    banner("config")
    required = {"META_API_KEY": s.meta_api_key, "META_BASE_URL": s.meta_base_url, "AGENT_MODEL": s.agent_model,
                "SAM_MODEL": s.sam_model, "META_SEGMENT_PATH": s.meta_segment_path}
    if s.ai_provider.strip().lower() == "gemini":
        required["GEMINI_API_KEY"] = s.gemini_api_key
    elif s.ai_provider.strip().lower() == "meta":
        required["META_CHAT_PATH"] = s.meta_chat_path
    else:
        required["AI_PROVIDER (gemini or meta)"] = ""
    if not sam_responses.is_responses_path(s.meta_segment_path):
        required["META_SEGMENT_MASKS_PATH"] = s.meta_segment_masks_path
    missing = [k for k, v in required.items() if not v]
    if not ck.record("config", not missing, "all required settings present" if not missing else "missing: " + ", ".join(missing)):
        if sam_responses.is_responses_path(s.meta_segment_path):
            print("Set the missing provider settings from the official Meta docs, then run `probe`.")
        else:
            print("Run `probe` first to discover META_SEGMENT_MASKS_PATH; fill the rest from the Meta docs.")
    client = get_meta_client()

    earlier_path = _abs(args.earlier)
    png, rgb = load_png(earlier_path, s.max_image_dim)
    print(f"earlier image: {earlier_path.name} sent as {rgb.shape[1]}x{rgb.shape[0]} PNG ({len(png)/1024:.0f} KiB)")

    banner("chat")
    def chat_plain():
        t0 = time.perf_counter()
        r = client.chat([{"role": "user", "content": "Reply with exactly the word OK."}], model=s.agent_model)
        ck.record("chat", bool(r.content and r.content.strip()), f"{time.perf_counter()-t0:.2f}s, reply={ (r.content or '')[:60]!r}")
    ck.guard("chat", chat_plain)

    banner("JSON response parsing")
    def structured():
        r = client.chat(
            [{"role": "user", "content": 'Return only valid JSON with one boolean field: {"ok": true}.'}],
            model=s.agent_model,
        )
        parsed = json.loads(r.content or "")
        ck.record("json_response", parsed.get("ok") is True, f"parsed={parsed}")
    ck.guard("json_response", structured)

    banner("sam segmentation")
    masks_by_prompt: dict[str, np.ndarray] = {}
    sam_rows = []
    for prompt in prompts:
        def one(prompt=prompt):
            t0 = time.perf_counter()
            masks = client.segment(png, prompt)
            dt = time.perf_counter() - t0
            union = np.zeros(rgb.shape[:2], dtype=bool)
            for m in masks:
                union |= m
            st = mask_stats(union, prompt if prompt in s.qc_bounds else "vegetation")
            masks_by_prompt[prompt] = union
            Image.fromarray(union.astype(np.uint8) * 255).save(OUT / f"mask_{prompt}.png")
            (OUT / f"overlay_{prompt}.png").write_bytes(make_overlay(rgb, union, prompt))
            row = {"prompt": prompt, "instances": len(masks), "pixels": st.pixel_count,
                   "coverage": round(st.coverage, 5), "latency_s": round(dt, 2), "qc_suspicious": st.suspicious, "qc_reasons": st.reasons}
            sam_rows.append(row)
            print(f"  {prompt!r}: {len(masks)} instance(s), {st.pixel_count} px ({st.coverage:.2%}), {dt:.1f}s"
                  + (f"  [QC would flag: {'; '.join(st.reasons)}]" if st.suspicious else ""))
        ck.guard("sam", one)
    ck.record("sam", bool(masks_by_prompt) and any(r["pixels"] > 0 for r in sam_rows),
              f"{len(masks_by_prompt)}/{len(prompts)} prompts decoded; masks saved to gate0_out/", rows=sam_rows)
    ck.record("overlay", any((OUT / f"overlay_{p}.png").exists() for p in prompts),
              "inspect gate0_out/overlay_*.png by eye: is the tinted region plausible?")

    provider_name = "Gemini" if s.ai_provider.strip().lower() == "gemini" else "Muse Spark"
    banner(f"vision review ({provider_name} looks at image + overlay)")
    def vision():
        prompt = next((p for p in prompts if p in masks_by_prompt and masks_by_prompt[p].any()), None)
        if prompt is None:
            ck.record("vision_review", False, "no non-empty mask to review")
            return
        ov = (OUT / f"overlay_{prompt}.png").read_bytes()
        ok, issues, suggestion = vision_review(client, vision_model, png, ov, prompt if prompt in s.qc_bounds else "vegetation", prompt)
        parseable = not any("unparseable" in i for i in issues)
        ck.record("vision_review", parseable, f"prompt={prompt!r} acceptable={ok} issues={issues} suggested={suggestion!r}")
    ck.guard("vision_review", vision)

    banner("tool-call round trip")
    def tool_call():
        tool = {"name": "count_detected_pixels", "description": "Return the number of pixels SAM detected for a prompt in the earlier image.",
                "parameters": {"type": "object", "properties": {"prompt": {"type": "string"}}, "required": ["prompt"]}}
        target = prompts[0]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "Use the provided tool for any measurement. Never guess numbers."},
            {"role": "user", "content": f"How many pixels were detected for '{target}' in the earlier image? Use the tool, then answer in one sentence."},
        ]
        executed = 0
        final = None
        for _ in range(4):
            r = client.chat(messages, model=s.agent_model, tools=[tool])
            if not r.tool_calls:
                final = r.content
                break
            messages.append({"role": "assistant", "content": r.content, "tool_calls": [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}} for c in r.tool_calls]})
            for c in r.tool_calls:
                px = int(masks_by_prompt.get(c.arguments.get("prompt", target), np.zeros(1, bool)).sum()) if c.name == tool["name"] else -1
                executed += c.name == tool["name"]
                messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps({"pixels": px})})
        ck.record("tool_call", executed >= 1 and bool(final), f"tool executed {executed}x; final answer={ (final or '')[:120]!r}")
    ck.guard("tool_call", tool_call)

    if args.newer:
        banner("alignment (app validation on your pair)")
        def align():
            from app.services.validation import prepare_pair
            pp = prepare_pair(earlier_path.read_bytes(), _abs(args.newer).read_bytes())
            v = pp.validation
            print(json.dumps(json.loads(v.model_dump_json()), indent=2))
            ck.record("alignment", v.verdict != "reject", f"verdict={v.verdict} method={v.method} inliers={v.inliers} overlap={v.overlap_ratio}")
            Image.fromarray(pp.newer).save(OUT / "newer_warped_to_earlier.png")
        ck.guard("alignment", align)

    (OUT / "gate0_report.json").write_text(json.dumps(ck.results, indent=2, default=str))
    banner("summary")
    for k, v in ck.results.items():
        print(f"  {'PASS' if v['ok'] else 'FAIL'}  {k}")
    print("\nGATE 0:", "PASSED" if ck.all_ok else "NOT PASSED", "-> details in gate0_out/gate0_report.json")
    print("Also eyeball gate0_out/overlay_*.png: a passing script with a nonsense mask is not a pass.")
    return 0 if ck.all_ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe", help="post one SAM request and print a sanitized summary for Responses mode")
    p.add_argument("--image", required=True)
    p.add_argument("--prompt", default="buildings")
    r = sub.add_parser("run", help="run the full Gate 0 checklist")
    r.add_argument("--earlier", required=True)
    r.add_argument("--newer")
    r.add_argument("--prompts", default="buildings,trees,vegetation")
    args = ap.parse_args(_ARGS_PRE)
    try:
        return cmd_probe(args) if args.cmd == "probe" else cmd_run(args)
    except MetaApiError as exc:
        print(f"\nProvider error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
