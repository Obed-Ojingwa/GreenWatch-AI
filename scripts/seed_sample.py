#!/usr/bin/env python3
"""Create the public, read-only SAMPLE investigation from a REAL analysis run.

Judges can open the sample without a demo key. It must be produced with real provider responses
(never synthetic data): this script drives the app's own HTTP routes, so the images are validated, the
agent runs with the configured AI and SAM clients, and the sample is only flagged public if the run finishes.

    cd backend
    python ../scripts/seed_sample.py \\
        --title "Example site, Lagos" --location "Lekki, Lagos" \\
        --objective "Investigate potential building change between the two images" \\
        --earlier /path/earlier.jpg --earlier-date 2023-02-01 \\
        --newer /path/newer.jpg --newer-date 2024-02-01 \\
        --source "Own drone survey" --categories buildings,trees

Point DATABASE_URL / STORAGE_BACKEND (and the Gemini and Meta SAM settings) at the target environment first. To seed
production, run it locally with the production DATABASE_URL and Supabase storage settings in the shell.
Only imagery you own or have the right to publish may be used (see docs/data-sources.md).
"""
from __future__ import annotations

import argparse
import mimetypes
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
ORIG_CWD = Path.cwd()


def _abs(p: str) -> Path:
    q = Path(p).expanduser()
    return (q if q.is_absolute() else ORIG_CWD / q).resolve()


args_pre = sys.argv[1:]
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import get_settings, production_problems  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title", required=True)
    ap.add_argument("--location", required=True)
    ap.add_argument("--objective", required=True)
    ap.add_argument("--earlier", required=True)
    ap.add_argument("--earlier-date")
    ap.add_argument("--newer", required=True)
    ap.add_argument("--newer-date")
    ap.add_argument("--source", required=True, help="public source and attribution recorded for the imagery")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lng", type=float)
    ap.add_argument("--categories", default="buildings")
    ap.add_argument("--confirm-public-rights", action="store_true",
                    help="confirm both source images and derived outputs are cleared for public use")
    ap.add_argument("--accept-review-required", action="store_true",
                    help="also publish a sample whose run ended 'review_required' (it will be shown as such)")
    args = ap.parse_args(args_pre)

    if not args.confirm_public_rights:
        print("Refusing to seed: pass --confirm-public-rights only after confirming image and output rights.")
        return 2

    s = get_settings()
    if s.environment.lower() != "production":
        print("Refusing to seed: ENVIRONMENT must be 'production'.")
        return 2
    problems = production_problems(s)
    if problems:
        print("Refusing to seed: production configuration is unsafe or incomplete:")
        for problem in problems:
            print(f"  - {problem}")
        return 2
    if not (s.meta_api_key and s.meta_base_url and s.agent_model and s.sam_model):
        print("AI/SAM provider configuration is incomplete; finish Gate 0 first.")
        return 2
    if not s.demo_access_key:
        print("DEMO_ACCESS_KEY is empty; set it so the seeding requests are authorised.")
        return 2

    from app.main import app

    headers = {"X-Demo-Key": s.demo_access_key}
    cats = [c.strip() for c in args.categories.split(",") if c.strip()]
    with TestClient(app, base_url="http://seed.local") as c:
        r = c.post("/api/investigations", headers=headers, json={
            "title": args.title, "location_name": args.location, "objective": args.objective,
            "latitude": args.lat, "longitude": args.lng})
        r.raise_for_status()
        inv_id = r.json()["id"]
        print("investigation:", inv_id)

        for role, path, d in (("earlier", args.earlier, args.earlier_date), ("newer", args.newer, args.newer_date)):
            p = _abs(path)
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            data = {"role": role}
            if d:
                data["capture_date"] = d
            if args.source:
                data["source"] = args.source
            with open(p, "rb") as fh:
                r = c.post(f"/api/investigations/{inv_id}/images", headers=headers, data=data,
                           files={"file": (p.name, fh, ctype)})
            if r.status_code >= 400:
                print(f"upload {role} failed: {r.status_code} {r.text}")
                return 1
            print(f"uploaded {role}: {p.name}")

        print("running the agent with the real Meta client (this takes a while)...")
        r = c.post(f"/api/investigations/{inv_id}/agent-runs", headers=headers, json={"categories": cats})
        if r.status_code >= 400:
            print(f"could not start run: {r.status_code} {r.text}")
            return 1
        run_id = r.json()["id"]
        r = c.get(f"/api/agent-runs/{run_id}", headers=headers)
        run = r.json()
        print("run status:", run["status"])
        for st in run.get("steps", []):
            print(f"  {st['idx']:>2} {st['tool']:<26} {st['status']:<8} {st.get('duration_ms') or '-'} ms")
        if run.get("error"):
            print("error:", run["error"])

    ok = run["status"] == "completed" or (args.accept_review_required and run["status"] == "review_required")
    if not ok:
        print(f"\nNOT publishing as the sample (status {run['status']}). The investigation {inv_id} stays private.")
        return 1

    from app.db.models import Investigation
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        inv = db.get(Investigation, __import__("uuid").UUID(inv_id))
        inv.is_sample = True
        db.commit()
    finally:
        db.close()
    print(f"\nSample published: investigation {inv_id}, run {run_id}. It is now readable without a demo key.")
    print("Open it in the frontend and check it by eye before recording the demo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
