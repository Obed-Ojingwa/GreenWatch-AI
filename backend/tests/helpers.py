"""Test-only helpers and a FAKE Meta client. Never imported by application code."""
from __future__ import annotations

import io
import json
import uuid
from datetime import date

import cv2
import numpy as np
from PIL import Image as PILImage

from app.core.errors import MetaApiError
from app.db.models import Image, Investigation
from app.services.meta_client import ChatResponse, ChatToolCall
from app.services.validation import encode_png

MAGENTA = (255, 0, 255)  # "buildings" in synthetic scenes (random scene colours never reach R=255)
GREEN = (0, 255, 0)  # "vegetation"


def scene(seed=0, size=600):
    rng = np.random.default_rng(seed)
    img = np.full((size, size, 3), 110, np.uint8)
    for _ in range(250):
        x, y = rng.integers(0, size, 2)
        r = int(rng.integers(4, 22))
        c = tuple(int(v) for v in rng.integers(30, 230, 3))
        cv2.circle(img, (int(x), int(y)), r, c, -1)
        cv2.rectangle(img, (int(x), int(y)), (int(x) + r, int(y) + r), c, 1)
    return cv2.GaussianBlur(img, (3, 3), 0)


def warp(img, angle=2.0, scale=1.02, tx=8, ty=-6):
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, scale)
    M[:, 2] += (tx, ty)
    return cv2.warpAffine(img, M, (w, h), borderMode=cv2.BORDER_REFLECT)


def make_pair(seed=1):
    """Earlier: 3 buildings + 1 green patch. Newer (warped): 2 extra buildings, green patch shrunk."""
    a = scene(seed)
    for x, y in [(60, 60), (200, 80), (400, 120)]:
        cv2.rectangle(a, (x, y), (x + 50, y + 50), MAGENTA, -1)
    cv2.rectangle(a, (100, 400), (260, 520), GREEN, -1)
    b = a.copy()
    for x, y in [(300, 300), (450, 420)]:
        cv2.rectangle(b, (x, y), (x + 50, y + 50), MAGENTA, -1)
    cv2.rectangle(b, (100, 400), (260, 460), GREEN, -1)  # vegetation loss in the lower strip
    cv2.rectangle(b, (100, 460), (260, 520), (110, 110, 110), -1)
    return encode_png(a), encode_png(warp(b))


def make_investigation(db, storage, earlier: bytes, newer: bytes, categories=("buildings",),
                       georef=None, dates=(date(2023, 1, 1), date(2024, 1, 1))) -> Investigation:
    inv = Investigation(title="Test site", location_name="Lagos", objective="Investigate potential change")
    db.add(inv)
    db.flush()
    for role, data, d in (("earlier", earlier, dates[0]), ("newer", newer, dates[1])):
        path = f"investigations/{inv.id}/{role}.png"
        storage.upload_bytes(path, data, "image/png")
        w, h = PILImage.open(io.BytesIO(data)).size
        db.add(Image(investigation_id=inv.id, role=role, storage_path=path, capture_date=d,
                     source="synthetic", width=w, height=h, sha256=uuid.uuid4().hex * 2,
                     georef_meta=georef))
    db.commit()
    return inv


def new_run(db, inv, categories=("buildings",), approval=False):
    from app.db.models import AnalysisRun
    run = AnalysisRun(investigation_id=inv.id, categories=list(categories), approval=approval)
    db.add(run)
    db.commit()
    return run


class FakeMeta:
    """Scripted stand-in for Muse Spark + SAM, used only in tests."""

    def __init__(self, interp_mode="good", vision=None, fail_segment=False, trees_empty=True):
        self.interp_mode = interp_mode
        self.vision = vision or (lambda prompt: (True, [], None))
        self.fail_segment = fail_segment
        self.trees_empty = trees_empty
        self.segment_calls = 0
        self.vision_calls = 0
        self.chat_calls = 0
        self._bad_sent = False

    # -- SAM ----------------------------------------------------------------------------------
    def segment(self, image_png, prompt):
        self.segment_calls += 1
        if self.fail_segment:
            raise MetaApiError("Meta API 503: unavailable", 503)
        arr = np.asarray(PILImage.open(io.BytesIO(image_png)).convert("RGB")).astype(int)
        if prompt in ("buildings", "roofs"):
            target = MAGENTA
        elif prompt == "vegetation":
            target = GREEN
        elif prompt == "trees":
            if self.trees_empty:
                return []
            target = GREEN
        else:
            return []
        mask = (np.abs(arr - np.array(target)).sum(axis=2) < 90)
        return [mask] if mask.any() else []

    # -- chat ---------------------------------------------------------------------------------
    def chat(self, messages, *, model, tools=None, images=None, json_schema=None):
        self.chat_calls += 1
        if images:
            self.vision_calls += 1
            text = messages[-1]["content"]
            prompt = text.split("prompted with '")[1].split("'")[0]
            ok, issues, sp = self.vision(prompt)
            return ChatResponse(content=json.dumps({"acceptable": ok, "issues": issues, "suggested_prompt": sp}))
        return self._agent_turn(messages)

    def _agent_turn(self, messages):
        user = messages[1]["content"]
        cats = [c.strip().rstrip(".") for c in user.split("Categories to analyse:")[1].split(",")]
        calls, results = {}, []
        for m in messages:
            for tc in m.get("tool_calls") or []:
                calls[tc["id"]] = (tc["function"]["name"], json.loads(tc["function"]["arguments"]))
            if m["role"] == "tool":
                n, a = calls[m["tool_call_id"]]
                results.append((n, a, json.loads(m["content"])))

        def ok(name, **kw):
            return [o for n, a, o in results if n == name and "error" not in o
                    and all(a.get(k) == v for k, v in kw.items())]

        n_id = [0]

        def call(name, **args):
            n_id[0] += 1
            return ChatResponse(tool_calls=[ChatToolCall(id=f"c{len(results)}_{n_id[0]}", name=name, arguments=args)])

        if not ok("get_investigation"):
            return call("get_investigation")
        if not ok("validate_image_pair"):
            return call("validate_image_pair")
        if ok("validate_image_pair")[-1]["verdict"] == "reject":
            return ChatResponse(content="Stopped: the image pair is not suitable for comparison.")

        checks = {o["seg_id"]: o for n, a, o in results if n == "check_mask_quality" and "error" not in o}
        for c in cats:
            for role in ("earlier", "newer"):
                segs = ok("segment_image", role=role, category=c)
                if not segs:
                    return call("segment_image", role=role, category=c)
                last = segs[-1]
                if last["seg_id"] not in checks:
                    return call("check_mask_quality", seg_id=last["seg_id"])
                chk = checks[last["seg_id"]]
                if chk["status"] == "retry":
                    return call("segment_image", role=role, category=c, prompt=chk["suggested_prompt"])
        for c in cats:
            if not ok("compare_masks", category=c):
                return call("compare_masks", category=c)
            if not ok("calculate_change_metrics", category=c):
                return call("calculate_change_metrics", category=c)
        if not ok("save_analysis_results"):
            return call("save_analysis_results")
        if self.interp_mode == "never":
            return ChatResponse(content="Done.")
        for c in cats:
            if not ok("submit_interpretation", category=c):
                m = ok("calculate_change_metrics", category=c)[-1]
                evid = [f"cmp:{c}"]
                summary = (f"Potential change detected: detected {c} pixels go from {m['old_pixels']:,} to "
                           f"{m['new_pixels']:,}; requires human review.")
                if self.interp_mode == "bad_evidence" and not self._bad_sent:
                    self._bad_sent = True
                    evid = ["seg:does-not-exist", f"cmp:{c}"]
                if self.interp_mode == "illegal" and not self._bad_sent:
                    self._bad_sent = True
                    summary = "This looks like illegal construction."
                if self.interp_mode == "invented_number" and not self._bad_sent:
                    self._bad_sent = True
                    summary = "Roughly 87% of the area was cleared."
                return call("submit_interpretation", category=c, summary=summary,
                            change_direction=m["change_direction"], evidence_ids=evid,
                            limitations=["Real-world area is not established."],
                            recommended_action="Inspect the highlighted regions on site.",
                            requires_human_review=True)
        if not ok("generate_report"):
            return call("generate_report")
        return ChatResponse(content="Investigation complete: potential change detected; requires human review.")
