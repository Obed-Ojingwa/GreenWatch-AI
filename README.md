# GreenWatch AI

**See environmental change. Measure it. Know what to do next.**

GreenWatch helps an environmental officer or GIS analyst investigate *potential* change between two images of the same site. An agent validates the pair, segments buildings, trees or vegetation with **Meta SAM 3.1**, checks the quality of its own segmentation with **Muse Spark** vision (retrying with a different prompt when a mask looks wrong), computes pixel-level change **in code**, writes an evidence-linked PDF report, and proposes a human-approved inspection task.

Built solo for the Meta Global AI Developer Hackathon (Indie Developer track, "Build for Your World").

> It reports *potential change in detected pixels* and always requires human review. It never states or implies that anything is illegal, and it claims square metres only when the imagery is georeferenced in a projected CRS with a known pixel size.

## Status

The application is built and tested locally. **It has not yet been run against the live Meta API.** See [`docs/REMAINING.md`](docs/REMAINING.md) for exactly what is verified, what is not, and what is left to do before submission.

## What was built during the hackathon

Everything in this repository was built between Oct 27 and Nov 4, 2026, from scratch, by one person. No code, data or assets from any employer or prior product are used. (Keep this section accurate, and commit often: the git history is the evidence.)

## How it works

```
upload two images -> validate and align the pair (ORB + RANSAC) -> SAM 3.1 per category
   -> quality control (coverage statistics, then Muse Spark vision review; retry or flag)
   -> compare masks and compute metrics in code -> validated interpretation
   -> PDF report (full | needs review | diagnostic) -> approved inspection task
```

- **Backend computes every number.** Muse Spark orchestrates tool calls and explains results; its text is validated against the stored metrics.
- **The agent cannot override a failed check**, run SQL, touch files or change permissions. It can only call a fixed set of typed tools, with prerequisites enforced by the backend and a cap on calls.
- **Failures are honest.** A Meta API failure marks the run `failed` and produces no report; an untrustworthy run is `review_required` and its report says so.

More detail: [`docs/architecture.md`](docs/architecture.md).

## Stack

React + TypeScript + Tailwind (Vite) | FastAPI + Pydantic + SQLAlchemy | PostgreSQL (Supabase) + Supabase Storage | OpenCV, NumPy, Pillow, ReportLab | Meta Model API (SAM 3.1, Muse Spark) | Vercel + Render.

Verified in development with Python 3.13, Node 22, FastAPI 0.162, SQLAlchemy 2.1, Pydantic 2.13, PostgreSQL 16, React 19, Vite 8.

## Run it locally

```bash
# backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # then edit; see "Configure Meta" below
python -m uvicorn app.main:app --reload --port 8000

# frontend (second terminal)
cd frontend
npm install
cp .env.example .env            # VITE_API_BASE_URL=http://localhost:8000
npm run dev
```

Open http://localhost:5173. Write actions ask for the access key (`DEMO_ACCESS_KEY` in `backend/.env`). Local development uses SQLite and local file storage; nothing else to set up.

### Tests

```bash
cd backend && python -m pytest -q      # Meta is faked; no network or credits needed
cd frontend && npm run typecheck && npm run build
```

### Configure Meta (Gate 0)

The Meta adapter is configuration-driven and **unverified until you run Gate 0**:

```bash
cd backend
python ../scripts/gate0.py probe --image /path/to/earlier.jpg      # shows the raw SAM response shape
python ../scripts/gate0.py run --earlier earlier.jpg --newer newer.jpg
```

Record the findings in [`docs/meta-api-notes.md`](docs/meta-api-notes.md).

### Development data (never for the demo)

`python ../scripts/dev_fake_run.py` creates a clearly labelled synthetic investigation with a scripted fake model so you can see the UI before Gate 0. Its output must never appear in the demo or the submission.

## Deploy

- **Database (Supabase):** create a project and run [`supabase_migrations/20261030164200_initial_schema.sql`](supabase_migrations/20261030164200_initial_schema.sql) once in the SQL editor (it mirrors `backend/app/db/schema.sql`). Create a **private** Storage bucket named `greenwatch`; do not add public bucket policies.
- **Backend (Render):** create the web service from [`render.yaml`](render.yaml). Set its dashboard environment values: `DATABASE_URL` (Supabase Postgres connection string with SSL enabled), `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `DEMO_ACCESS_KEY` (random, at least 16 characters), exact deployed `FRONTEND_ORIGIN`, and the verified Meta values from Gate 0 (`META_API_KEY`, `META_BASE_URL`, `META_AUTH_HEADER`, `META_AUTH_SCHEME`, `META_CHAT_PATH`, `META_SEGMENT_PATH`, `META_SEGMENT_BODY`, `META_SEGMENT_MASKS_PATH`, `SAM_MODEL`, `AGENT_MODEL`, and optional `VISION_QC_MODEL`). Keep all credentials on Render; the Supabase service key must never be set in Vercel. Production startup refuses unsafe core configuration (weak demo key, SQLite, local storage, localhost CORS).
- **Frontend (Vercel):** import the repository with root directory `frontend/`; set only `VITE_API_BASE_URL` to the deployed Render API URL. Never expose Meta or Supabase credentials as `VITE_*` variables.
- **Smoke checks:** after you deploy, visit Render `/health`, then use the app to create an investigation and upload an authorized test image with the demo key. Confirm the image/report links load from the private bucket via signed URLs. In Supabase, verify the bucket is still private and unauthenticated object access is denied. Then verify writes fail without the demo key and succeed with it. Share sanitized status codes/log excerpts only; never share credentials or signed URLs.
- **Public sample:** `python ../scripts/seed_sample.py ...` from a *real* run (see the script header).

## Demo access

The sample investigation is public and read-only. Starting a new analysis needs the demo access key, which is given in the submission notes (it protects the model credits behind the app).

## Limitations

Pixel-level change is not real-world area unless the imagery is georeferenced in a projected CRS. Alignment is one global transform with an overlap check, not tile-level registration. Segmentation quality depends on the imagery; the AI-assisted quality check reduces but does not remove the need for human review.

## Layout

```
backend/    FastAPI app (app/), tests (tests/), schema (app/db/schema.sql)
frontend/   React app
scripts/    gate0.py, seed_sample.py, dev_fake_run.py
docs/       architecture, build plan, remaining work, Meta API notes, data sources, demo script
render.yaml Render blueprint for the API
```

## License

This project is distributed under the MIT License; see [`LICENSE`](LICENSE).
