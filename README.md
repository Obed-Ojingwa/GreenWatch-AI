# GreenWatch AI

**See environmental change. Measure it. Know what to do next.**

GreenWatch helps an environmental officer or GIS analyst investigate *potential* change between two images of the same site. An agent validates the pair, segments buildings, trees or vegetation with **Meta SAM 3.1**, checks the segmentation with **Gemini** vision (retrying with a different prompt when a mask looks wrong), computes pixel-level change **in code**, writes an evidence-linked PDF report, and proposes a human-approved inspection task.

Built solo for the Meta Global AI Developer Hackathon (Indie Developer track, "Build for Your World").

> It reports *potential change in detected pixels* and always requires human review. It never states or implies that anything is illegal, and it claims square metres only when the imagery is georeferenced in a projected CRS with a known pixel size.

## Status

Release status remains **unverified**. The latest owner-reported Render health response was `status=ok`, `environment=production`, `database=ready`, and sanitized API logs showed successful reads; these checks do not prove provider access or an end-to-end run. Gemini chat/vision and Meta SAM now have separate configuration. No real Gemini/SAM Gate 0 result or production sample is verified here. Confirm provider access and imagery rights before analysis or publication; record attribution in [`docs/data-sources.md`](docs/data-sources.md).

## What was built during the hackathon

This project was built solo for the Meta Global AI Developer Hackathon (Indie Developer track, "Build for Your World"). No code, data or assets from any employer or prior product are used.

## How it works

```
upload two images -> validate and align the pair (ORB + RANSAC) -> SAM 3.1 per category
   -> quality control (coverage statistics, then Gemini vision review; retry or flag)
   -> compare masks and compute metrics in code -> validated interpretation
   -> PDF report (full | needs review | diagnostic) -> approved inspection task
```

- **Backend computes every number.** Gemini orchestrates tool calls and explains results; its text is validated against the stored metrics. Meta SAM generates the segmentation masks.
- **The agent cannot override a failed check**, run SQL, touch files or change permissions. It can only call a fixed set of typed tools, with prerequisites enforced by the backend and a cap on calls.
- **Failures are honest.** An AI or SAM provider failure marks the run `failed` and produces no report; an untrustworthy run is `review_required` and its report says so.

More detail: [`docs/architecture.md`](docs/architecture.md).

## Stack

React + TypeScript + Tailwind (Vite) | FastAPI + Pydantic + SQLAlchemy | PostgreSQL (Supabase) + Supabase Storage | OpenCV, NumPy, Pillow, ReportLab | Gemini API (agent and vision QC), Meta Model API (SAM 3.1 segmentation) | Vercel + Render.

Verified in development with Python 3.13, Node 22, FastAPI 0.162, SQLAlchemy 2.1, Pydantic 2.13, PostgreSQL 16, React 19, Vite 8.

## Run it locally

```bash
# backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # then edit; see "Configure AI providers" below
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
cd backend && python -m pytest -q      # provider clients are faked; no network or credits needed
cd frontend && npm run typecheck && npm run build
```

### Configure AI providers (Gate 0)

Gemini is the primary provider for agent tool calls and vision QC. Create a Gemini API key in [Google AI Studio](https://aistudio.google.com/apikey); set `GEMINI_API_KEY` only in the ignored `backend/.env` for local work or Render's backend environment dashboard for production. Set `AI_PROVIDER=gemini` and `AGENT_MODEL` to a model ID available to that key. Do not put provider keys in Vercel, `VITE_*`, source control, or chat.

Meta SAM remains a separate segmentation provider. Keep its credentials and configuration in the same backend-only locations as `META_API_KEY`, `META_BASE_URL`, `META_SEGMENT_PATH`, and `SAM_MODEL`. Gemini does not generate this app's pixel masks, so a working SAM configuration is still required for end-to-end runs. Verify both providers on the selected imagery before describing a run as validated:

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
- **Backend (Render):** create the web service from [`render.yaml`](render.yaml). For `DATABASE_URL`, use the Supabase **Session pooler** URI (Dashboard → Connect → Session pooler), which supports IPv4-only networks. Supabase direct `db.<project-ref>.supabase.co:5432` connections require IPv6 unless the project has the IPv4 add-on. Copy the dashboard-provided pooler host, username, port, and SSL options exactly; substitute the database password locally and URL-encode reserved characters. Set `AI_PROVIDER=gemini`, `GEMINI_API_KEY`, `AGENT_MODEL` (available Gemini model ID), `META_API_KEY` (Meta SAM access), `META_BASE_URL`, `META_SEGMENT_PATH`, `SAM_MODEL`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `DEMO_ACCESS_KEY` (random, at least 16 characters), and `FRONTEND_ORIGIN` (comma-separated deployed origins). Optionally set `VISION_QC_MODEL` to use a separate Gemini model. Render environment variables override settings defaults. SAM's Responses mode does not require `META_SEGMENT_BODY` or `META_SEGMENT_MASKS_PATH`; those apply only to legacy JSON endpoints. Keep all credentials on Render; the Supabase service key must never be set in Vercel. Production startup refuses unsafe or incomplete configuration.
- Create the Gemini key in [Google AI Studio](https://aistudio.google.com/apikey) and set it as `GEMINI_API_KEY` in Render. Keep it out of Vercel, `VITE_*`, source control, and chat. `META_API_KEY` is a separate backend-only credential for Meta SAM segmentation.
- **Runtime AI diagnosis (optional):** if Render Shell is unavailable, set `META_DIAGNOSTIC_KEY` to a separate random secret in Render, deploy, then POST to `/api/ai/diagnostics` with header `X-Meta-Diagnostic-Key`. This makes one billable minimal chat request and reports only the configured provider, host/path/model, status, and error code. Gemini's OpenAI-compatible API does not expose a documented model-catalog endpoint, so the probe validates Gemini with a request rather than listing models. It is rate-limited to two requests per hour. Remove `META_DIAGNOSTIC_KEY` after diagnosis; do not send the diagnostic key in chat.
- **Frontend (Vercel):** import the repository with root directory `frontend/`; set only `VITE_API_BASE_URL` to the deployed Render API URL. Never expose Gemini, Meta, or Supabase credentials as `VITE_*` variables.
- **Smoke checks:** after you deploy, verify Render `/health` returns `200`, `environment=production`, and `database=ready`; then verify `/api/stats` and `/api/investigations` return JSON `200` and the Vercel-origin preflight succeeds. Only then use the app to create an investigation and upload an authorized test image with the demo key. Confirm the image/report links load from the private bucket via signed URLs. In Supabase, verify the bucket is still private and unauthenticated object access is denied. Then verify writes fail without the demo key and succeed with it. Share sanitized status codes/log excerpts only; never share credentials or signed URLs.
- **Public sample:** the sample must use a real provider run and imagery with confirmed public-use rights. `scripts/seed_sample.py` creates a new investigation and calls the configured Gemini and Meta SAM providers; it is not a no-cost way to publish an existing run. After production is verified, configure the local ignored `backend/.env` for the production database/storage and both provider integrations, then run the script with `--confirm-public-rights`. This may incur provider costs; do not paste or commit those credentials.

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
