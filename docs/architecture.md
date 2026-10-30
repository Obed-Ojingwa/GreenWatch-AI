# Architecture

```
Browser (React + TypeScript + Tailwind, Vercel)
    |  REST, polling every 1-2 s; X-Demo-Key header for write routes
    v
FastAPI (Render)  ----------------------------------------------+
  routes: investigations | runs | files                          |
  services:                                                      |
    agent.py        tool loop + guardrails (Muse Spark)          |
    validation.py   pair checks, ORB + RANSAC alignment          |
    segmentation.py SAM calls, cache, overlays                   |
    quality.py      mask stats + vision review                   |
    comparison.py   pure NumPy metrics                           |
    report.py       report gate + ReportLab PDF                  |
    meta_client.py  ALL Meta HTTP (config-driven)                |
    mask_codec.py   PNG / RLE / polygon / raster mask decoding   |
    storage.py      local (dev) | Supabase Storage (prod)        |
    |                         |                                  |
    v                         v                                  v
Postgres (Supabase)     Object storage (private bucket)   Meta Model API
7 tables                 images, masks, overlays, PDFs     SAM 3.1, Muse Spark
```

A modular monolith. Analyses run as FastAPI background tasks; the frontend polls `GET /api/agent-runs/{id}`.

## The honesty rules (enforced in code)

1. **Backend computes every number; models only explain.** The model's interpretation is validated server-side: unknown evidence IDs, a direction that contradicts the computed one, or text with numbers/claims that the stored metrics do not support are rejected. If the model never submits a valid interpretation, the backend writes a deterministic one from the stored metrics.
2. **Pixels, not metres.** Square metres are produced only for a projected CRS with a known positive pixel size.
3. **Percent change is undefined when the earlier count is zero**; the absolute difference is reported instead.
4. **A failed pair check cannot be overridden by the agent.** A `reject` verdict stops the run; a marginal one continues as `review_required`.
5. **Segmentation QC is AI-assisted, not a guarantee.** Backend coverage statistics run first, then a Muse Spark vision review of image + overlay. A bad mask is retried with a different prompt up to `MAX_QC_RETRIES`, then flagged for human review. An unparseable review counts as "not acceptable".
6. **A Meta API failure marks the run `failed` and produces no report.** Runs that finish but are not trustworthy end as `review_required` and produce a clearly labelled report.
7. **Wording is "potential change detected, requires human review".** Never illegality, cause or blame.

## Run statuses

`queued` -> `processing` -> `completed` | `review_required` | `failed`

`failed` means a system error (timeout, API failure). `review_required` means it ran but the pair or masks cannot be fully trusted. Stale `processing` runs are marked `failed` on startup.

## Agent tools (the only things the model can call)

`get_investigation`, `validate_image_pair`, `segment_image`, `check_mask_quality`, `compare_masks`, `calculate_change_metrics`, `save_analysis_results`, `submit_interpretation`, `generate_report`, `create_inspection_task` (only available after user approval). Arguments are validated with Pydantic, prerequisites are enforced in the backend (for example `compare_masks` refuses until quality control has run), and the number of calls per run is capped (`MAX_TOOL_CALLS_PER_RUN`). The model cannot run SQL, read files or change its own permissions.

## Reports

`full` (completed), `needs_review` (review required) or `diagnostic` (analysis not completed; states no finding). No report is generated for a `failed` run. All figures in the PDF come from stored metrics.

## Data model

`investigations`, `images`, `analysis_runs` (validation, metrics, findings, interpretation, status), `segmentation_results` (per image, category, prompt, attempt, QC status, mask and overlay paths), `agent_steps` (powers the timeline), `reports`, `inspection_tasks`. Schema: `backend/app/db/schema.sql` (a test checks it against the SQLAlchemy models).

For a new Supabase project, apply `supabase_migrations/20261030164200_initial_schema.sql` once. It is tested against the canonical schema; do not rerun it over an existing installation.

## API

| Method | Path | Access |
|---|---|---|
| GET | `/health` | public |
| POST | `/api/investigations` | demo key |
| GET | `/api/investigations`, `/api/investigations/{id}` | sample public; others need the key |
| POST | `/api/investigations/{id}/images` | demo key |
| POST | `/api/investigations/{id}/agent-runs` | demo key, rate limited |
| GET | `/api/agent-runs/{id}` | sample public; others need the key |
| POST | `/api/agent-runs/{id}/approve-task`, `/report` | demo key |
| GET | `/api/reports/{id}`, `/api/tasks`, `/api/stats` | sample public; others need the key |
| PATCH | `/api/tasks/{id}` | demo key |
| GET | `/api/files/{path}` | HMAC-signed URL (local storage) |

## Security model (demo mode)

No user accounts. A public, read-only sample investigation; every write or analysis route requires `X-Demo-Key`. IDs are UUIDs; storage paths are never returned (short-lived signed URLs only); uploads are type, size and pixel-count limited; images cannot be replaced after a run exists; the Meta key and Supabase service key live only in the backend environment. In production the app refuses to start with an unsafe configuration.
