# GreenWatch AI

**See environmental change. Measure it. Know what to do next.**

GreenWatch helps an environmental officer or GIS analyst investigate *potential* change between two images of the same site. An agent validates the pair, segments buildings, trees or vegetation with **Meta SAM 3.1**, checks the quality of its own segmentation with **Muse Spark** vision (retrying with a different prompt when a mask looks wrong), computes pixel-level change **in code**, writes an evidence-linked PDF report, and proposes a human-approved inspection task.

Built solo for the Meta Global AI Developer Hackathon (Indie Developer track, "Build for Your World").

> It reports *potential change in detected pixels* and always requires human review. It never states or implies that anything is illegal, and it claims square metres only when the imagery is georeferenced in a projected CRS with a known pixel size.

## Status

Release status is **blocked**: on Nov 3, 2026, Render `/health` returned `200` with `environment=development`; an `OPTIONS` preflight from the deployed Vercel origin returned `400` without an allow-origin header; and the public investigation list returned `200` with zero entries. Set `ENVIRONMENT=production` and `FRONTEND_ORIGIN=https://greenwatch-ai-theta.vercel.app` in the Render service dashboard, redeploy, then repeat the health/CORS checks. Do not run production smoke tests or seed a public sample until they pass. No real-provider Gate 0 result or production sample is verified here. A prior local imagery pair was authorized for public use; its source and attribution still need to be recorded in [`docs/data-sources.md`](docs/data-sources.md).

## What was built during the hackathon

This project was built solo for the Meta Global AI Developer Hackathon (Indie Developer track, "Build for Your World"). No code, data or assets from any employer or prior product are used.

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

The Meta key belongs in the ignored `backend/.env` file for a local Gate 0 run, or in the Render service's environment dashboard for deployment. Do not put it in Vercel, `VITE_*`, source control, or chat. The documented SAM Responses contract is implemented, but verify it against your account with Gate 0 before describing a live run as validated:

```bash
cd backend
python ../scripts/gate0.py probe --image /path/to/earlier.jpg      # prints sanitized mask counts/dimensions
python ../scripts/gate0.py run --earlier earlier.jpg --newer newer.jpg
```

The Responses probe does not print or save raw mask-token output. The legacy JSON probe writes raw response data under ignored `gate0_out/`. Record only sanitized findings in [`docs/meta-api-notes.md`](docs/meta-api-notes.md); never record credentials, signed URLs, or private image data.

### Development data (never for the demo)

`python ../scripts/dev_fake_run.py` creates a clearly labelled synthetic investigation with a scripted fake model so you can see the UI before Gate 0. Its output must never appear in the demo or the submission.

## Deploy

- **Database (Supabase):** create a project and run [`supabase_migrations/20261030164200_initial_schema.sql`](supabase_migrations/20261030164200_initial_schema.sql) once in the SQL editor (it mirrors `backend/app/db/schema.sql`). Create a **private** Storage bucket named `greenwatch`; do not add public bucket policies.
- **Backend (Render):** create the web service from [`render.yaml`](render.yaml). Set its dashboard environment values: `DATABASE_URL` (Supabase Postgres connection string with SSL enabled), `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `DEMO_ACCESS_KEY` (random, at least 16 characters), exact deployed `FRONTEND_ORIGIN`, and verified Meta values (`META_API_KEY`, `META_BASE_URL`, `META_AUTH_HEADER`, `META_AUTH_SCHEME`, `META_CHAT_PATH`, `META_SEGMENT_PATH`, `SAM_MODEL`, `AGENT_MODEL`, and optional `VISION_QC_MODEL`). SAM's Responses mode does not require `META_SEGMENT_BODY` or `META_SEGMENT_MASKS_PATH`; those apply only to legacy JSON endpoints. Keep all credentials on Render; the Supabase service key must never be set in Vercel. Production startup refuses unsafe core configuration (weak demo key, SQLite, local storage, localhost CORS).
- **Frontend (Vercel):** import the repository with root directory `frontend/`; set only `VITE_API_BASE_URL` to the deployed Render API URL. Never expose Meta or Supabase credentials as `VITE_*` variables.
- **Smoke checks:** after you deploy, visit Render `/health`, then use the app to create an investigation and upload an authorized test image with the demo key. Confirm the image/report links load from the private bucket via signed URLs. In Supabase, verify the bucket is still private and unauthenticated object access is denied. Then verify writes fail without the demo key and succeed with it. Share sanitized status codes/log excerpts only; never share credentials or signed URLs.
- **Public sample:** the sample must use a real provider run and imagery with confirmed public-use rights. `scripts/seed_sample.py` creates a new investigation and calls the configured Meta API; it is not a no-cost way to publish an existing run. After production is verified, configure the local ignored `backend/.env` for the production database/storage and verified Meta settings, then run the script with `--confirm-public-rights`. This may incur provider costs; do not paste or commit those credentials.

## Demo access

Once an approved real run is explicitly designated as the sample, it is public and read-only. As of the latest production check, no sample is seeded. Starting a new analysis needs the demo access key, which belongs in submission notes only (it protects the model credits behind the app).

## Limitations

Pixel-level change is not real-world area unless the imagery is georeferenced in a projected CRS. Alignment is one global transform with an overlap check, not tile-level registration. Segmentation quality depends on the imagery; the AI-assisted quality check reduces but does not remove the need for human review.

The SAM adapter uses Meta's `meta-sam-parser` package (Meta SAM License); that dependency's terms are separate from this project's MIT license.

## Layout

```
backend/    FastAPI app (app/), tests (tests/), schema (app/db/schema.sql)
frontend/   React app
scripts/    gate0.py, seed_sample.py, dev_fake_run.py
docs/       architecture, Meta API notes, data sources, and demo script (planning docs remain local)
render.yaml Render blueprint for the API
```

## License

This project is distributed under the MIT License; see [`LICENSE`](LICENSE).
