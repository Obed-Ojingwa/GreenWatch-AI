# GreenWatch AI: Build Plan (Phases, Scope, Integration to Deployment)

> **Purpose of this file:** a single source of truth for any AI coding assistant (or human) working on this repo. Read Sections 0-3 fully before touching code. Then work **one phase at a time** using Section 4 onward. Keep Section 1 (STATUS) up to date.
> Derived from Blueprint v3. If this file and chat history disagree, this file wins until the owner says otherwise.

---

## 0. Instructions for the AI assistant

1. **Read STATUS (Section 1)** to find the current phase. Work only on that phase unless told otherwise.
2. **Never invent Meta API details.** Model IDs, endpoints, auth, image-input format, SAM response/mask format and tool-calling format are all `VERIFY`. Ask the owner to paste the relevant Meta docs or the output of `scripts/gate0.py`. Put verified facts in `docs/meta-api-notes.md`.
3. **Backend code computes numbers. Models explain them.** No measurement, percentage or area may originate from a model's text output.
4. **Do not build anything in Section 3 (Out of scope).**
5. **No ENY OS code, data, or assets.** This repo is a fresh hackathon build (rule: new work only, public repo).
6. **Never commit secrets.** Only `.env.example`. Never put `META_API_KEY` or the Supabase service key anywhere the browser can reach.
7. After finishing a task: tick its checkbox, run its acceptance check, and commit small and often (the git history is evidence of work done during the event).
8. If something in a phase is blocked by an unknown (usually a Meta API detail), stop and ask; do not stub it with fake data and move on silently. Mocks are allowed only in unit tests, never in demo paths.
9. When you finish a phase, update STATUS and state: what works, what was cut, what the next phase needs from the owner.

---

## 1. STATUS (update after every session)

| Field | Value |
|---|---|
| **Current phase** | **Phase 0: Gate 0, blocked on real Meta credentials and imagery.** Phases 1-7 are built and tested locally; deployment config and docs are written. See `docs/REMAINING.md` for exactly what is left. |
| Last updated | Fri Oct 30, 2026 (Lagos time) |
| Hard deadline | Nov 4, 9:00 PM ET = **Nov 5, 3:00 AM Lagos**. Treat **Nov 3 night** as the real deadline |
| Track | Indie Developer, solo |
| Gate 0 passed? | No. `scripts/gate0.py` is ready (`probe` then `run`); it has only been exercised against local stand-ins. |
| Imagery chosen? | No |
| Meta credits verified? | Unknown (owner to confirm in Billing -> free credits) |
| Repo created? | Local folder with an empty git repo (no commits yet). Commit a baseline now. |

> **As built vs this plan (differences to know about):** the agent has an extra `submit_interpretation` tool and calls `generate_report` itself; `MAX_TOOL_CALLS_PER_RUN` defaults to 40, not 12 (two categories with retries need more); masks decode through `mask_codec.py` (PNG, COCO RLE, polygon, raster) driven by `META_SEGMENT_*` settings instead of a hard-coded format; `docs/RECOVERY_NOTES.md` explains files that were rebuilt early on. The authoritative description of the code is `docs/architecture.md` and the test suite in `backend/tests/`, not the section texts below.

### Phase checklist
- [ ] Phase 0: Setup and Gate 0 (Oct 28)
- [ ] Phase 1: Comparison core, tested (Oct 29 AM)
- [ ] Phase 2: Database, storage, backend skeleton (Oct 29)
- [ ] Phase 3: Meta client, segmentation, validation, QC services (Oct 29-30)
- [ ] Phase 4: Agent loop (Oct 30)
- [ ] Phase 5: API routes and demo-mode security (Oct 30)
- [ ] Phase 6: Frontend (Oct 31 - Nov 1)
- [ ] Phase 7: Report and inspection tasks (Nov 1)
- [ ] Phase 8: Deployment (Nov 2)
- [ ] Phase 9: Demo video, README, submission (Nov 3, buffer Nov 4)

**Cut order if behind:** inspection-task UI polish -> history page polish -> overlay toggle polish. **Never cut:** real SAM overlays, the agent loop with QC, the PDF report, the deployed demo.

---

## 2. Product and scope (frozen)

**GreenWatch AI** helps an environmental officer or GIS analyst investigate potential change between two images of one site (earlier and newer). An agent validates the pair, segments buildings/trees (and experimentally vegetation) with **SAM 3.1**, reviews its own segmentation quality, computes pixel-level change in backend code, writes an evidence-linked **PDF report**, and creates a human **inspection task** after user approval. **Muse Spark** orchestrates the tool calls and explains validated results.

**Honesty rules (hard-coded, not prompts):**
- Pixel metrics only, unless georeferenced with projected CRS and known resolution (then m2 allowed).
- If old pixel count = 0 -> report absolute difference, no percentage.
- Mask shape mismatch -> reject.
- Failed alignment/overlap -> `review_required` or stop. The agent cannot override it.
- Meta API failure -> run `failed`, **no report**.
- Never state or imply illegality, causation or unlawful activity. Wording: "potential change detected, requires human review".

**Hackathon rules that constrain design:** public repo; built during the event (Oct 27 - Nov 4); imagery must be owned or openly licensed (no Google Earth screenshots); must meaningfully use Meta Model API (SAM 3.1 + Muse Spark); avoid "simple image analyzer"; 3-minute real demo video; judges must see what was built during the event.

### In scope (must have)
1. Create investigation + upload two images with capture dates, source, location
2. Image-pair validation (size, dimensions, alignment, overlap)
3. SAM 3.1 segmentation with per-category prompts, cached by image hash + prompt
4. Mask quality check (backend stats, then Muse Spark vision), up to 2 retries with alternate prompts
5. Backend mask comparison and metrics
6. Muse Spark agent loop with real tool calls, persisted step log
7. Analysis workspace UI: before/after, overlays toggle, metrics, live agent timeline, findings/limitations
8. PDF report (ReportLab template), gated on validated analysis
9. Inspection task (create after approval, status toggle)
10. Investigation history (list, reopen)
11. Restricted demo mode: public read-only sample; write/analysis endpoints behind a demo key
12. Deployed demo + public repo + docs

## 3. Out of scope (do not build)
Full authentication/multi-user/orgs, GIS map, NDVI/multispectral, satellite ingestion, video analysis, notifications, Alembic, Docker, Muse Image/Voice, custom model training, legal determinations, any mobile app.
**Optional only after Phase 8 passes:** Muse Spark Open orchestration via Baseten (open-weights bonus); simple map marker view.

---

## 3b. Stack and conventions

| Layer | Choice |
|---|---|
| Frontend | React 18 + TypeScript + Vite + Tailwind CSS; React Router; TanStack Query (polling); no UI kit required |
| Backend | Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2.x (sync is fine), `httpx`, `python-multipart`, `slowapi` (rate limit) |
| Image/analysis | Pillow, NumPy, OpenCV (`opencv-python-headless`), optional Rasterio only if the chosen imagery is a GeoTIFF |
| Database | PostgreSQL on Supabase (no PostGIS needed). Schema applied via one `schema.sql` (no Alembic) |
| Object storage | Supabase Storage (private bucket `greenwatch`, signed URLs for access) |
| AI | Meta Model API: SAM 3.1 (segmentation), Muse Spark (agent + vision QC + narrative). Agent model ID is a **config value** |
| PDF | ReportLab |
| Tests | pytest (backend). Vitest optional for frontend |
| Deploy | Frontend on Vercel, backend on Render, DB + storage on Supabase |
| Secrets | `.env` locally, platform env vars in production. Only `.env.example` committed |

**Conventions**
- Typed everywhere: Pydantic schemas for all request/response/tool I/O; TypeScript types mirror the API.
- Services are pure modules with no FastAPI imports, so they can be tested alone.
- IDs are UUIDs (unguessable).
- All timestamps UTC (`timestamptz`).
- Masks stored as 1-bit/8-bit PNG in storage; DB stores path + pixel count + coverage ratio.
- Every agent tool call writes an `agent_steps` row (input, output, status, duration).
- Errors: raise typed domain errors (`ValidationFailed`, `MetaApiError`, `UnsupportedMeasurement`), map to HTTP codes in one place.

### Repo layout
```
greenwatch-ai/
├─ frontend/
│  ├─ src/
│  │  ├─ pages/ (Dashboard, NewInvestigation, Workspace, Tasks)
│  │  ├─ components/ (ImageCompare, OverlayToggle, AgentTimeline, MetricsTable, FindingCard, TaskList)
│  │  ├─ services/api.ts
│  │  ├─ hooks/ (useAgentRun polling)
│  │  ├─ types/
│  │  └─ main.tsx, App.tsx
│  └─ package.json, vite.config.ts, tailwind.config.ts
├─ backend/
│  ├─ app/
│  │  ├─ main.py
│  │  ├─ core/ (config.py, security.py [demo key], errors.py)
│  │  ├─ db/ (session.py, models.py, schema.sql)
│  │  ├─ schemas/ (pydantic)
│  │  ├─ api/routes/ (investigations.py, images.py, agent_runs.py, reports.py, tasks.py)
│  │  └─ services/
│  │     ├─ meta_client.py        # all Meta HTTP calls live here
│  │     ├─ segmentation.py       # SAM call, mask decode, cache
│  │     ├─ validation.py         # pair validation + alignment
│  │     ├─ quality.py            # mask QC (stats + Spark vision)
│  │     ├─ comparison.py         # pure NumPy metrics
│  │     ├─ agent.py              # tool loop
│  │     ├─ tools.py              # tool registry (whitelist, schemas)
│  │     ├─ storage.py            # Supabase Storage wrapper
│  │     └─ report.py             # gate + ReportLab
│  ├─ tests/
│  ├─ requirements.txt
│  └─ .env.example
├─ scripts/ (gate0.py, seed_sample.py)
├─ docs/ (architecture.md, meta-api-notes.md, data-sources.md, demo-script.md, BUILD_PLAN.md)
├─ README.md, LICENSE, .gitignore
```

### Environment variables (`backend/.env.example`)
```
ENVIRONMENT=development
DATABASE_URL=postgresql+psycopg://...
SUPABASE_URL=
SUPABASE_SERVICE_KEY=          # backend only
STORAGE_BUCKET=greenwatch
META_API_KEY=                  # backend only
META_BASE_URL=                 # VERIFY from Meta docs
SAM_MODEL=                     # VERIFY
AGENT_MODEL=                   # VERIFY (Muse Spark ID); swappable to Spark Open later
VISION_QC_MODEL=               # VERIFY (usually same as AGENT_MODEL)
DEMO_ACCESS_KEY=               # protects write/analysis endpoints
FRONTEND_ORIGIN=http://localhost:5173
MAX_UPLOAD_MB=15
MAX_IMAGE_DIM=2048
MAX_AGENT_RUNS_PER_HOUR=20
MAX_TOOL_CALLS_PER_RUN=12
MAX_QC_RETRIES=2
```
Frontend: `VITE_API_BASE_URL` only. The demo key is entered by the user in the UI and sent as `X-Demo-Key`; it is never baked into the bundle.

---

## 4. PHASE 0: Setup and Gate 0 (Oct 28)

**Objective:** prove the Meta models work on your real imagery before building anything else.

**Owner tasks (human)**
- [ ] Confirm the $150 credit shows in Meta Billing -> free credits; set a spend-limit alert
- [ ] Request Baseten and Modal credits (optional, for the open-weights bonus)
- [ ] Choose one before/after imagery pair: your own drone/aerial data or openly licensed (OpenAerialMap etc.), clear usage rights, visible change, same site, ideally orthorectified. Record source + license in `docs/data-sources.md`
- [ ] Create the public GitHub repo today; first commit = scaffold

**AI tasks**
- [ ] Scaffold repo layout, `.gitignore`, `README.md` stub, `backend/.env.example`, `requirements.txt` (fastapi, uvicorn, pydantic, sqlalchemy, psycopg[binary], httpx, python-multipart, pillow, numpy, opencv-python-headless, reportlab, slowapi, pytest, python-dotenv, supabase or httpx for storage)
- [ ] Ask the owner for the Meta docs (auth, model list, SAM segmentation request/response, image input format, Muse Spark chat + vision + tool-calling). Save facts to `docs/meta-api-notes.md`
- [ ] Write `scripts/gate0.py` that:
  1. Loads config from `.env`
  2. Sends the **earlier** image to SAM 3.1 with prompt `buildings`, decodes the mask, saves `mask.png`, prints pixel count and coverage %
  3. Repeats with `trees` and `vegetation`
  4. Builds an overlay PNG (mask tinted over the image)
  5. Sends image + overlay to Muse Spark and asks a structured plausibility question (`{acceptable: bool, issues: [str]}`)
  6. Makes one real tool call through Muse Spark (e.g. a `count_pixels(path)` function), executes it locally, returns the result to the model
  7. Prints all raw errors; does not swallow exceptions

**Acceptance (Gate 0)**
- Real mask + pixel counts for at least one category on both images
- Overlay looks plausible by eye
- Muse Spark returns a parseable structured answer and completes one tool-call round trip
- Findings recorded in `docs/meta-api-notes.md` (model IDs, request shapes, mask format, rate limits, latency, cost per call)

**If it fails:** change prompt/imagery/format; if SAM is poor on vegetation, lead with buildings and treat vegetation as experimental. Do not start Phase 2 until Gate 0 passes.

---

## 5. PHASE 1: Comparison core, tested (Oct 29 AM)

**Objective:** a pure-Python, fully tested measurement module independent of any model or database.

- [ ] `services/comparison.py`: `compare_masks(old, new, valid=None) -> ComparisonResult` (pydantic): `old_pixels`, `new_pixels`, `difference_pixels`, `change_percent` (None if old=0), `change_direction` (increase|decrease|unchanged|undetermined), `metric_type="pixel_count"`, `coverage_old`, `coverage_new`
- [ ] `comparison.py`: `area_m2(pixels, georef)` allowed **only** if georef has projected CRS + positive pixel size; else raises `UnsupportedMeasurement`
- [ ] `comparison.py`: `diff_overlay(old, new) -> RGBA array` (gained/lost/unchanged colored)
- [ ] `services/quality.py` (stats part only for now): `mask_stats(mask, category) -> {empty, coverage, suspicious, reason}` using a per-category bounds table in config (tunable; no universal threshold)
- [ ] Tests A-H in `tests/test_comparison.py` (see Section 12)

**Acceptance:** `pytest` green; formula verified against hand-computed values (40,000 -> 30,000 = -25%); old=0 returns `change_percent=None`.

---

## 6. PHASE 2: Database, storage, backend skeleton (Oct 29)

**Objective:** persistent records and file storage with a running FastAPI app.

- [ ] Create Supabase project, private bucket `greenwatch`
- [ ] Apply `backend/app/db/schema.sql` (below) in the Supabase SQL editor
- [ ] `db/session.py`, `db/models.py` (SQLAlchemy models mirroring schema)
- [ ] `core/config.py` (pydantic-settings), `core/errors.py`, `core/security.py` (demo-key dependency, stub allowing all for now)
- [ ] `services/storage.py`: `upload_bytes(path, bytes, content_type)`, `download_bytes(path)`, `signed_url(path, ttl=3600)`
- [ ] `main.py`: app, CORS (from `FRONTEND_ORIGIN`), `/health`, exception handlers
- [ ] Startup hook: mark any `analysis_runs` stuck in `processing` older than 10 minutes as `failed` (background tasks die on Render restarts)

### Schema (`schema.sql`)
```sql
create extension if not exists "pgcrypto";

create table investigations (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  location_name text not null,
  latitude double precision,
  longitude double precision,
  objective text not null,
  notes text,
  is_sample boolean not null default false,
  status text not null default 'draft'
    check (status in ('draft','processing','completed','review_required','failed')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table images (
  id uuid primary key default gen_random_uuid(),
  investigation_id uuid not null references investigations(id) on delete cascade,
  role text not null check (role in ('earlier','newer')),
  storage_path text not null,
  capture_date date,
  source text,
  width int not null,
  height int not null,
  sha256 text not null,
  georef_meta jsonb,              -- {crs, pixel_size_m, bounds} or null
  created_at timestamptz not null default now(),
  unique (investigation_id, role)
);

create table analysis_runs (
  id uuid primary key default gen_random_uuid(),
  investigation_id uuid not null references investigations(id) on delete cascade,
  status text not null default 'queued'
    check (status in ('queued','processing','completed','review_required','failed')),
  categories text[] not null default '{buildings}',
  model_config jsonb not null default '{}',   -- model IDs, prompts used, retries, QC thresholds
  validation jsonb,                           -- alignment/overlap/pair checks
  metrics jsonb,                              -- per-category ComparisonResult
  findings jsonb,                             -- list of findings, each with evidence_refs + limitations
  interpretation jsonb,                       -- validated structured Muse Spark output
  error text,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now()
);

create table segmentation_results (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references analysis_runs(id) on delete cascade,
  image_id uuid not null references images(id) on delete cascade,
  category text not null,
  prompt_used text not null,
  attempt int not null default 1,
  mask_path text,
  overlay_path text,
  pixel_count int,
  coverage double precision,
  qc_status text check (qc_status in ('accepted','retry','flagged')),
  qc_detail jsonb,
  from_cache boolean not null default false,
  created_at timestamptz not null default now()
);

create table agent_steps (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references analysis_runs(id) on delete cascade,
  idx int not null,
  tool text not null,
  input jsonb,
  output jsonb,
  status text not null check (status in ('running','ok','error','skipped')),
  duration_ms int,
  created_at timestamptz not null default now(),
  unique (run_id, idx)
);

create table reports (
  id uuid primary key default gen_random_uuid(),
  investigation_id uuid not null references investigations(id) on delete cascade,
  run_id uuid not null references analysis_runs(id) on delete cascade,
  kind text not null check (kind in ('full','needs_review','diagnostic')),
  storage_path text not null,
  created_at timestamptz not null default now()
);

create table inspection_tasks (
  id uuid primary key default gen_random_uuid(),
  investigation_id uuid not null references investigations(id) on delete cascade,
  run_id uuid references analysis_runs(id) on delete set null,
  title text not null,
  reason text not null,
  priority text not null default 'medium' check (priority in ('low','medium','high')),
  status text not null default 'open' check (status in ('open','in_progress','done')),
  created_at timestamptz not null default now()
);

create index on images (investigation_id);
create index on analysis_runs (investigation_id);
create index on agent_steps (run_id, idx);
create index on segmentation_results (run_id);
```

**Acceptance:** `uvicorn app.main:app` runs; `/health` returns ok; a script can insert an investigation, upload bytes to storage, and read a signed URL back.

---

## 7. PHASE 3: Services: Meta client, segmentation, validation, QC (Oct 29-30)

**Objective:** each capability works standalone with tests, before the agent touches it.

### 7.1 `meta_client.py`
- [ ] Single place for HTTP to Meta. Methods: `segment(image_bytes, prompt) -> raw`, `chat(messages, tools=None, images=None, response_schema=None) -> response`
- [ ] Timeouts, retry with backoff on 429/5xx (max 3), raise `MetaApiError` with status + body
- [ ] Log model ID, latency, token/credit usage when available
- [ ] **VERIFY everything against `docs/meta-api-notes.md`.** No guessed endpoints

### 7.2 `segmentation.py`
- [ ] `segment_image(image_bytes, category, prompt) -> SegResult` (decoded bool mask, pixel_count, coverage)
- [ ] Mask decoder per verified Meta response format; if multiple instances, union them into one mask
- [ ] Cache: key = `sha256(image_bytes) + ":" + prompt + ":" + SAM_MODEL`; store raw/decoded mask in storage and a row in `segmentation_results` (`from_cache=true` on hit). Demo recording uses the cache
- [ ] Downscale images to `MAX_IMAGE_DIM` before sending; keep original untouched in storage; record scale factor
- [ ] Save mask PNG + overlay PNG to storage

### 7.3 `validation.py`
- [ ] `validate_image_pair(earlier, newer, georef) -> PairValidation`: both decodable, dimensions within limits, dates present (warn if missing), same aspect (warn), georef consistency if present
- [ ] Alignment hierarchy:
  1. If both have georef with the same CRS and compatible bounds -> `aligned=true, method="georef"` (resample to common grid if needed)
  2. Else ORB + BFMatcher + RANSAC homography. Pass only if: inlier ratio >= threshold, transform plausible (bounded scale/rotation/shear), overlap area >= threshold after warp
  3. Return `{aligned, method, inlier_ratio, overlap_ratio, transform, warnings, verdict: ok|review_required|reject}`
- [ ] If aligned via homography, warp the newer image onto the earlier image's grid before segmentation and record the transform; compute a `valid_mask` (overlap region)
- [ ] One global transform plus an overlap check only (no tile-level alignment)

### 7.3b `quality.py` (QC)
- [ ] Stats gate (from Phase 1): empty mask or coverage outside per-category bounds -> `suspicious`
- [ ] Vision review: send image + overlay to Muse Spark with a structured schema `{acceptable: bool, issues: [str], suggested_prompt: str|null}`
- [ ] Decision: `accepted` if stats ok and vision acceptable; else `retry` with alternate prompt (`trees<->vegetation`, `buildings<->roofs`) up to `MAX_QC_RETRIES`; after the limit -> `flagged` and run becomes `review_required`
- [ ] Log original result, each retry, final status. Label in UI/report: "AI-assisted segmentation QC", never "verified"

**Acceptance:** unit tests for validation verdicts (aligned pair ok, unrelated pair rejected, low-overlap flagged); a script segments both images through the cache and re-running is instant; QC flags a synthetic all-ones mask.

---

## 8. PHASE 4: Agent loop (Oct 30)

**Objective:** Muse Spark orchestrates real backend tools; every step is logged; guardrails enforced in code.

### Tool registry (`tools.py`): the only things the agent can call
| Tool | Input | Output | Notes |
|---|---|---|---|
| `get_investigation` | none (run-scoped) | metadata + image refs | read-only |
| `validate_image_pair` | none | PairValidation | sets `validation` on run |
| `segment_image` | `role`, `category`, `prompt?` | seg id, pixel_count, coverage | cached |
| `check_mask_quality` | `seg_id` | `{status, issues, suggested_prompt}` | stats then vision |
| `compare_masks` | `category` | ComparisonResult | needs accepted/flagged masks for both roles |
| `calculate_change_metrics` | `category` | metrics + unit flags | m2 only if georef supports |
| `save_analysis_results` | none | findings with evidence_refs | persists |
| `generate_report` | none | report id + kind | gated (Phase 7) |
| `create_inspection_task` | `title`, `reason`, `priority` | task id | **only after user approval flag is set** |

### `agent.py`
- [ ] Loop: send system prompt + objective + tool schemas to Muse Spark; execute returned tool calls via the registry; append results; repeat until a final structured interpretation or limits hit
- [ ] Guardrails (code, not prompt): whitelist tools; validate arguments with Pydantic; `MAX_TOOL_CALLS_PER_RUN`; no SQL/file access; the agent **cannot** continue past a failed validation except as `review_required`; `create_inspection_task` rejected unless `approval` is true on the run
- [ ] **Controlled workflow:** the system prompt gives the intended order (get -> validate -> segment both -> QC -> compare -> metrics -> save -> interpret -> report -> ask approval), but the backend enforces prerequisites (e.g. `compare_masks` fails if QC not done)
- [ ] Final interpretation: structured JSON, validated server-side:
```json
{
  "summary": "string, no illegality claims",
  "change_direction": "increase|decrease|unchanged|undetermined",
  "category": "buildings|trees|vegetation",
  "evidence_ids": ["seg:<uuid>", "seg:<uuid>", "cmp:<category>"],
  "limitations": ["string"],
  "recommended_action": "string",
  "requires_human_review": true
}
```
  Reject if any `evidence_id` does not resolve to a stored record or any numeric claim appears that is absent from stored metrics; numbers shown to users are always rendered by the backend from `metrics`, never from this text
- [ ] Persist every call to `agent_steps` (running -> ok|error), update `analysis_runs.status`: `completed | review_required | failed`
- [ ] Run as FastAPI `BackgroundTasks`; endpoint returns the run id immediately
- [ ] Agent model read from `AGENT_MODEL` config so it can be swapped to Muse Spark Open later

**Acceptance:** one real end-to-end run from a script produces: ordered `agent_steps`, a `completed` run, stored metrics + findings with resolvable evidence refs; a forced bad pair ends in stop/`review_required`; a forced Meta failure ends `failed` with no report.

---

## 9. PHASE 5: API routes and demo-mode security (Oct 30)

**Objective:** the contract the frontend uses, with abuse protection.

```
GET  /health
POST /api/investigations                       (key)   create
GET  /api/investigations                       list (sample only unless key present)
GET  /api/investigations/{id}                  detail (sample public; others need key)
POST /api/investigations/{id}/images           (key)   multipart: role, capture_date, source, file
POST /api/investigations/{id}/agent-runs       (key)   body: {categories, objective?} -> {run_id}
GET  /api/agent-runs/{id}                      status, steps, metrics, findings, interpretation, image/overlay signed URLs
POST /api/agent-runs/{id}/approve-task         (key)   body: {title, reason, priority} -> creates inspection task
POST /api/agent-runs/{id}/report               (key)   regenerate report if needed
GET  /api/reports/{id}                         signed URL for PDF
GET  /api/tasks                                list
PATCH /api/tasks/{id}                          (key)   {status}
```
- [ ] Pydantic request/response models; consistent error body `{error, detail}`
- [ ] Upload checks: content type allowlist (jpeg/png/tiff), size <= `MAX_UPLOAD_MB`, decodable by Pillow, dimension limits, compute sha256, record width/height, extract georef if GeoTIFF (else null)
- [ ] `security.py`: `require_demo_key` dependency (`X-Demo-Key` header, constant-time compare) on all `(key)` routes
- [ ] Sample access: `is_sample=true` investigations readable without key; other IDs require the key (UUIDs unguessable)
- [ ] Rate limit `agent-runs` (slowapi, per IP + global cap); one active run per investigation (409 if already processing)
- [ ] Signed URLs for all images/masks/overlays/PDFs (short TTL); never return raw storage paths

**Acceptance:** curl/Postman walkthrough of the full flow; requests without the key to write routes return 401; second concurrent run returns 409.

---

## 10. PHASE 6: Frontend (Oct 31 - Nov 1)

**Objective:** the user can run and review a full investigation from the browser. Professional GIS-dashboard feel, not a chatbot.

- [ ] Vite + React + TS + Tailwind scaffold; `services/api.ts` (typed fetch wrapper, base URL from `VITE_API_BASE_URL`, attaches `X-Demo-Key` from a small "Enter access key" dialog stored in memory/sessionStorage)
- [ ] Types mirroring API schemas in `types/`
- [ ] **Dashboard:** counts (investigations, awaiting review, completed, open tasks) from real data; recent investigations; clearly labeled sample workspace when empty
- [ ] **New Investigation:** title, location, optional coordinates, objective, categories (buildings/trees/vegetation), earlier image + capture date, newer image + capture date, source; image previews; distinguish capture date from upload date; client-side size/type checks (server is authoritative)
- [ ] **Analysis Workspace (the demo screen):**
  - Before/after viewer (side-by-side + slider), overlay toggle per category and per image
  - **Agent timeline**: polls `GET /agent-runs/{id}` every 1-2 s, shows each step (tool, status, duration, short output), including real failures and QC retries
  - Metrics table (pixel counts, % only when defined, units explicit), validation/alignment badge, `review_required` banner
  - Findings with evidence links and limitations; interpretation text
  - Buttons: Generate/Download report; "Create inspection task" (opens approval form, then calls `approve-task`)
- [ ] **Tasks page:** list, status toggle
- [ ] Loading, empty and error states everywhere; no silent failures
- [ ] Never display "verified", "illegal", "violation"; use "potential change detected"
- [ ] Responsive enough to screen-record at 1080p; dark/light not required

**Acceptance:** from a fresh browser with the demo key, complete the flow upload -> run -> watch timeline -> view overlays/metrics -> report -> task; the sample investigation opens with no key.

---

## 11. PHASE 7: Report and inspection tasks (Nov 1)

### Report gate (`report.py`)
`generate_report(run_id)` accepts a **validated analysis object loaded from the DB**, never raw model prose. Checks before generating:
- [ ] Run status is `completed`, or `review_required` (produces a clearly labeled **needs-review** report)
- [ ] Required measurements exist; units explicit
- [ ] Every `evidence_ref` resolves to a stored record
- [ ] Alignment/QC warnings are included
- [ ] No m2 claims without georeferencing
- [ ] If checks fail -> **diagnostic report** (clearly states analysis not completed, lists failures) or no report; must never look like a successful assessment. If the run `failed` (e.g. Meta API error) -> no report

### Template (ReportLab)
Header + investigation/run IDs -> executive summary (validated interpretation text) -> before/after + overlay images -> metrics table (backend-rendered values only) -> findings with evidence IDs -> limitations -> recommended action -> review status and task status -> footer "Potential change detected; requires human review. Generated by GreenWatch AI".
- [ ] Store PDF in storage, row in `reports` (`kind`: full|needs_review|diagnostic), return signed URL
- [ ] Inspection task creation endpoint wired to the approval flow (title, reason, priority, link to investigation/run)

**Acceptance:** PDF opens, numbers match DB metrics exactly, a forced failure yields a diagnostic PDF or no PDF (never a normal-looking report).

---

## 12. Tests (write before or alongside the phase they cover)

| ID | Test | Phase |
|---|---|---|
| A | Identical masks -> zero difference | 1 |
| B | Synthetic masks with known counts -> exact diff and percent (40,000 -> 30,000 = -25.0) | 1 |
| C | Old = 0 -> `change_percent is None`, absolute difference reported | 1 |
| D | Shape mismatch -> rejected | 1 |
| E | Unaligned/unrelated pair -> `review_required` or reject | 3 |
| F | Meta API failure -> run `failed`, no report generated | 4/7 |
| G | No georef -> `area_m2` raises, no m2 in output/report | 1/7 |
| H | Interpretation with unknown `evidence_id` -> rejected | 4 |
| I | QC flags empty / near-full-coverage mask; retry limit ends in `flagged` | 3 |
| J | Write endpoints without demo key -> 401; concurrent run -> 409 | 5 |
| K | Report gate: failed run -> no report; review_required -> needs-review report | 7 |

Minimum: A-H pass before Phase 4 completes.

---

## 13. PHASE 8: Deployment (Nov 2)

- [ ] **Supabase:** production DB with `schema.sql` applied; bucket `greenwatch` private; service key only in Render env
- [ ] **Backend on Render (web service):** `pip install -r requirements.txt`; start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`; set all env vars (Section 3b); set `FRONTEND_ORIGIN` to the Vercel URL; note cold starts on free tier: hit `/health` before recording; confirm stuck-run cleanup on startup works
- [ ] **Frontend on Vercel:** root `frontend/`; build `npm run build`; set `VITE_API_BASE_URL` to the Render URL
- [ ] CORS verified from the Vercel origin only
- [ ] Verify: upload, long-running agent run (>30 s) completes, polling works, signed URLs load images and PDF, demo key gating works, rate limit works
- [ ] **Seed sample:** run `scripts/seed_sample.py` to create a precomputed `is_sample=true` investigation from real cached Meta responses (not mocks) so judges can open it without a key
- [ ] Billing check: Meta spend-limit alert set; credits applied; cache enabled so reruns cost nothing
- [ ] Full end-to-end test in production from a fresh browser/incognito window

**Optional (only if Phase 8 acceptance passes):** point `AGENT_MODEL` at Muse Spark Open (`meta-models/muse-spark-open-1026` on Baseten) and test tool calling; keep it only if reliable; document usage for the open-weights bonus.

---

## 14. PHASE 9: Demo, docs, submission (Nov 3; buffer Nov 4)

- [ ] README: what it is, architecture diagram, setup, env vars, how to use the demo key, **"What was built during the hackathon"** section, limitations, data sources and licenses
- [ ] `docs/architecture.md`, `docs/data-sources.md`, `docs/demo-script.md`
- [ ] Record the 3-minute demo from real (cached-but-real) responses; never present mocks as live; do not stage failures
  - 0:00-0:20 problem + the two images
  - 0:20-0:40 create investigation and upload
  - 0:40-1:30 run agent: live timeline, SAM overlays, QC confirming (or retrying) a mask
  - 1:30-2:00 metrics + limitations on screen
  - 2:00-2:30 PDF report
  - 2:30-2:50 approve inspection task
  - 2:50-3:00 impact line and next steps
- [ ] Verify the video link opens logged out; repo is public with no secrets in history (rotate keys if any leaked)
- [ ] Post the demo on X; submit the post link on the CV platform (Most Viral prize)
- [ ] Submit on the CV platform: Indie track, repo, video, live URL, demo key in notes. **Submit by Nov 3-4 afternoon Lagos time** (deadline Nov 5, 3:00 AM Lagos)

### Final submission checklist
- [ ] Credits applied, spend limit set
- [ ] Repo public, no secrets, README complete
- [ ] Live URL works from a fresh browser; sample opens without a key
- [ ] Write/analysis endpoints reject requests without the key
- [ ] 3-min video accessible logged out
- [ ] Participant listed (solo)
- [ ] Track: Indie
- [ ] X post link submitted
- [ ] Open-weights usage documented if used

---

## 15. Risks and fallbacks

| Risk | Response |
|---|---|
| SAM weak on vegetation | Lead with buildings/trees; show vegetation as experimental |
| No aligned pair | Use orthorectified/pre-registered imagery; show the agent refusing a bad pair as a feature, plus one convincing success |
| Meta API format differs from assumptions | Phase 0 notes drive everything; ask owner for docs/output; never guess |
| Latency/cost | Cache by hash; downscale to `MAX_IMAGE_DIM`; check Billing daily |
| Render cold start / restart kills background task | Startup cleanup marks stale runs `failed`; warm `/health` before demo; persisted steps allow resume-by-rerun |
| Spark Open tool calling flaky | Stay on Muse Spark; open-weights is optional |
| Abuse of public endpoints | Demo key, rate limits, upload caps, UUIDs, sample read-only |
| Scope creep | Section 3 is frozen |
