# GreenWatch AI: Blueprint v3 (Hackathon Build)

**Builder:** Obed (solo) | **Track:** Indie Developer | **Theme:** Build for Your World
**Deadline:** Nov 4, 9:00 PM ET (= Nov 5, 3:00 AM Lagos). **Real deadline: Nov 3 night.**
**Bonus targets:** Best Agentic Use-Case ($5K), Best Build on Meta Open Weight Models ($10K), Community Vote, Most Viral (X post)

---

## 0. Rules compliance (check before every decision)

| Rule | What it means for GreenWatch |
|---|---|
| New work only | Fresh repo created Oct 28. No ENY OS code. Commit daily so history proves it. |
| Public repo | No secrets committed. `.env.example` only. |
| Rights to assets | Imagery must be your own drone data or openly licensed (e.g. OpenAerialMap). No Google Earth screenshots. |
| Anti-projects | Avoid "simple image analyzer". The self-correcting agent loop (Section 3) is the differentiator. No medical, political, or war content. |
| Demo | 3 min, real model responses only, shows only what was built in the event. |
| Judging stage 1 | Must meaningfully use Meta Model API (SAM 3.1 + Muse Spark). |

## 1. Product in one paragraph

An environmental officer uploads two images of the same site from different dates. GreenWatch's agent validates the pair, segments buildings/trees with SAM 3.1, **checks its own segmentation quality using Muse Spark's vision, retries if bad**, computes change metrics in backend code, refuses to claim anything the data can't support, writes an evidence-linked PDF report, and creates an inspection task for a human.

**Honesty rule:** Backend code computes numbers. Muse Spark explains them. Never claim square metres without georeferencing. Never claim illegality.

## 2. Scope (frozen)

### Must have
1. Create investigation + upload 2 images with dates/source/location
2. Image-pair validation (size, dimensions, alignment check)
3. SAM 3.1 segmentation (buildings, trees; vegetation as an experiment)
4. Mask comparison and pixel metrics (backend)
5. Muse Spark agent with real tool calls and self-check/retry
6. Analysis workspace: before/after, overlays toggle, metrics, live agent timeline
7. PDF report (single ReportLab template)
8. Inspection task (create + status toggle)
9. Investigation history (list + reopen)
10. Deployed demo + public repo + 3-min video
11. Restricted demo mode: public read-only sample investigation; upload/analysis endpoints behind a demo access key (Section 6b)

### Cut (do not build)
Auth, map, Alembic, NDVI, satellite ingestion, video analysis, notifications, multi-user, Muse Image/Voice.

### Only if done early (priority order)
1. Muse Spark Open orchestration on Baseten (open-weights bonus)
2. Seeded sample investigation judges can open without an account
3. Simple Leaflet marker view

## 3. The agent loop (your differentiator)

```
START
 1. get_investigation()
 2. validate_image_pair()          -> if unsuitable: STOP and explain
 3. segment_image(earlier, prompt)
 4. segment_image(newer, prompt)
 5. check_mask_quality()          -> (a) backend stats, then (b) Muse Spark VISION reviews overlay vs image
       plausible?  yes -> continue
                   no  -> retry segment_image with alternate prompt
                          ("trees" <-> "vegetation", "buildings" <-> "roofs")
                          max 2 retries, then status = review_required (log original result, retry, final status)
 6. compare_masks()                -> backend
 7. calculate_change_metrics()     -> backend, with validation flags
 8. save_analysis_results()
 9. interpret_results()            -> Muse Spark, structured JSON
10. generate_report()
11. ask user -> create_inspection_task()   (requires approval)
END
```

**Rules**
- Max 12 tool calls per run; each tool has a whitelist and typed schema.
- Agent cannot run SQL, touch other files, or set its own permissions.
- Every timeline step maps to a real call and shows real failures.
- The agent cannot override a failed validation (alignment, overlap, empty masks). It can only stop, or continue as `review_required` with the warning recorded.
- Never stage a failure for the demo. Show actual behavior; if the first mask passes QC, show the QC confirming it.
- Structured output is validated server-side; every `evidence_id` must exist in the DB.
- The model name is a config value (`AGENT_MODEL`) so you can swap Muse Spark and Muse Spark Open.

### Tools (real functions)
| Tool | Owner | Notes |
|---|---|---|
| `get_investigation` | backend | metadata + image refs |
| `validate_image_pair` | OpenCV | size match, ORB feature matching, alignment score |
| `segment_image` | SAM 3.1 | returns decoded bool mask; cached by `sha256(image)+prompt` |
| `check_mask_quality` | backend stats, then Muse Spark vision | Stats first: mask non-empty, coverage % inside per-category bounds (tuned on your imagery, no universal threshold). Vision input: image + overlay; output: `{acceptable, issues, suggested_prompt}`. Call it AI-assisted segmentation QC, never a guarantee of accuracy |
| `compare_masks` | NumPy | old/new/diff pixels, % change (None if old = 0) |
| `calculate_change_metrics` | backend | pixel metrics; m² only if georeferenced |
| `save_analysis_results` | DB | persists run, masks, findings |
| `generate_report` | ReportLab | template + validated narrative |
| `create_inspection_task` | DB | only after user approval |

## 4. Architecture

```
React + TS + Tailwind (Vercel)
        |  REST + polling
FastAPI (Render)  ── services/
   meta_client.py  segmentation.py  comparison.py  agent.py  report.py  validation.py
        |                 |
 Supabase Postgres   Supabase Storage (images, masks, overlays, PDFs)
        |
 Meta Model API (SAM 3.1, Muse Spark)  [+ Baseten for Spark Open, optional]
```

Modular monolith. Analysis runs as a FastAPI background task; the frontend polls `/agent-runs/{id}`.

## 5. Data model (4 core tables + steps)

- `investigations` (id, title, location_name, lat, lng, objective, status, created_at)
- `images` (id, investigation_id, role[earlier|newer], storage_path, capture_date, source, width, height, sha256, georef_meta jsonb)
- `analysis_runs` (id, investigation_id, status, model_config jsonb, validation jsonb, findings jsonb, metrics jsonb, error, created_at)
  - `status`: queued | processing | completed | review_required | failed
  - `failed` = system error (timeout, API failure). `review_required` = ran fine but the pair or masks are not trustworthy.
  - `model_config`: SAM and agent model IDs, prompts used, retry count, QC thresholds. Lets you reproduce a run.
  - each finding in `findings` carries `evidence_refs`: image IDs, mask paths, comparison result, validation warnings
- `agent_steps` (id, run_id, idx, tool, input jsonb, output jsonb, status, duration_ms): powers the timeline
- `inspection_tasks` (id, investigation_id, title, reason, priority, status, created_at)
- `reports` (id, investigation_id, run_id, storage_path, created_at)

Masks and overlays live in object storage; DB stores paths and pixel counts.

## 6. API

```
POST /api/investigations              GET /api/investigations        GET /api/investigations/{id}
POST /api/investigations/{id}/images
POST /api/investigations/{id}/agent-runs   GET /api/agent-runs/{id}  (status + steps + results)
POST /api/agent-runs/{id}/report           GET /api/reports/{id}
POST /api/investigations/{id}/tasks        PATCH /api/tasks/{id}
GET  /api/tasks
```

Rate-limit `agent-runs`. Enforce file type/size on upload (15 MB).

## 6b. Restricted demo mode (protects your credits and card)

Your Meta account has a card attached, so open endpoints are a real cost risk. No full auth system, just:
- Public, read-only **precomputed sample investigation** that anyone (judges) can open.
- All write and analysis endpoints require a server-side **demo access key** (env var, checked in a FastAPI dependency). Put the key in the README and submission notes so judges can run a live analysis.
- Rate limit `agent-runs`, cap uploads (15 MB, max dimensions), cap total storage.
- Use unguessable IDs (UUIDs); list endpoints return only the sample unless the key is present.
- Meta API key and Supabase service key live only on the backend. Spend-limit alert on. Check Billing daily.

## 7. Measurement rules (hard-coded in `comparison.py` and `validation.py`)

| Situation | Allowed output |
|---|---|
| Ordinary photos, not aligned | Flag unreliable; pixel stats shown with warning, no "loss" claim |
| Aligned, no georeference | Pixel counts and % change only |
| Georeferenced + projected CRS + known resolution | m² estimates allowed |
| Old pixels = 0 | Absolute difference, no % |
| Mask shape mismatch | Reject |
| Meta API failure | Run marked failed; **no report generated** |

Percent change = (new − old) / old × 100, reported as *detected pixels*, never "real-world area" unless georeferenced.

### 7b. Alignment hierarchy (strict)

1. Prefer already-aligned or orthorectified pairs.
2. Otherwise estimate alignment with OpenCV (ORB + homography via RANSAC).
3. Validate the result: inliers ratio above a threshold, plausible transform (no extreme scale/skew), and overlap area above a threshold. A matching score alone is not enough.
4. Fail validation -> `review_required` (or stop). The agent cannot override it.
5. Match features on stable structures where possible; trees change between dates and are poor anchors. Evaluate building and vegetation comparisons separately.
6. Keep it simple: one global transform plus an overlap check. Do not build tile-level alignment checks.

## 8. Imagery plan (critical path)

1. **Tonight:** find a before/after pair of one site (drone/aerial, ideally Lagos) with a visible change and clear usage rights. Sources: your own drone/Pix4D data, OpenAerialMap, imagery you can license for demo.
2. Prefer pairs that are already orthorectified or register well with OpenCV.
3. Run SAM on both **buildings** and **trees/vegetation**. Lead the demo with whichever segments cleanly.
4. Fallback if no Lagos pair: any location with legal, high-res before/after. Say so honestly in the demo; the product is the workflow.
5. Sentinel-2 (10 m) is only a fallback for broad vegetation; too coarse for buildings/trees.

## 9. Gate 0 (tonight): do not build UI until this passes

One script `scripts/gate0.py`:
1. Authenticate to Meta Model API
2. Send your real image to SAM 3.1, decode the mask
3. Count pixels, save overlay
4. Send image + overlay to Muse Spark, get a plausibility judgment
5. Complete one real tool call via Muse Spark

**Pass:** masks are plausible on your imagery. **Fail:** change imagery/prompts or pivot the headline (buildings vs vegetation) tomorrow morning.

Also tonight: confirm the $150 shows under Billing → free credits, set a spend-limit alert, request Baseten and Modal credits.

## 10. Schedule (Lagos time)

| Date | Goal | Done when |
|---|---|---|
| **Wed Oct 28** | Imagery + Gate 0 + repo created + credits verified | Script prints mask pixel counts and a Spark tool call result |
| **Thu Oct 29** | Backend skeleton, DB, upload, SAM service with cache, comparison + unit tests | Tests A–G pass; one SAM-to-metrics run saved in DB |
| **Fri Oct 30** | Agent loop with self-check/retry, step logging, validation rules | `POST /agent-runs` completes an investigation end to end |
| **Sat Oct 31** | Frontend: new-investigation form, analysis workspace, timeline, overlays | Full run visible in browser (local) |
| **Sun Nov 1** | PDF report, inspection tasks, history page | Report downloads with real evidence |
| **Mon Nov 2** | Deploy (Vercel/Render/Supabase), end-to-end prod test, seeded sample run, try Spark Open | Public URL works from a fresh browser |
| **Tue Nov 3** | Polish, README, record + edit 3-min video, post demo on X | Video uploaded and accessible, repo public |
| **Wed Nov 4** | **Buffer only.** Submit by afternoon Lagos time | Submission confirmed on CV platform |

**Technical gates (must pass in order, each time-boxed to the dates above):**
G0 Meta calls work (Oct 28) -> G1 comparison module + tests A-H (Oct 29 AM) -> G2 backend workflow (Oct 29) -> G3 agent loop + failure paths (Oct 30) -> G4 UI (Oct 31) -> G5 report, demo mode, deploy (Nov 1-2). If a gate overruns by half a day, cut from Section 2 instead of moving the deadline.

If behind: cut tasks → history → polish in that order. Never cut agent loop, real overlays, or the report.

## 11. Tests (write these before wiring the agent)

A identical masks → zero diff | B synthetic known counts | C old=0 → no % | D shape mismatch rejected | E unaligned pair flagged | F Meta failure → run failed, no report | G no georef → no m² claim | H model output with unknown `evidence_id` rejected

## 12. Report (single template)

Header and IDs → executive summary (model, validated) → before/after + overlays → metrics table (pixels; % only if defined) → findings with evidence IDs → limitations → recommended action → review status. Backend fills numbers; Muse Spark supplies only validated narrative.

### 12b. Report gate

`generate_report` accepts a validated analysis object, never raw model prose. Before generating, the backend checks:
- run status is `completed` (or `review_required`, which yields a clearly labeled "needs review" report)
- required measurements exist and units are explicit
- every `evidence_ref` resolves to a stored record
- alignment warnings are carried into the report
- no m² claims without georeferencing

Enforce "narrative can't contradict numbers" by construction: Muse Spark returns structured output (enums, evidence IDs, short text), and the backend renders every number itself from stored metrics. If any check fails, produce a **diagnostic report** that cannot be mistaken for a successful assessment, or no report.

## 13. Demo script (3:00, all real responses)

- 0:00–0:20 Problem + the two images
- 0:20–0:40 Create investigation, upload
- 0:40–1:30 Run agent: live timeline, SAM overlays, **the self-check catching or confirming a mask (retry if it happens naturally)**
- 1:30–2:00 Metrics + limitations shown on screen
- 2:00–2:30 PDF report
- 2:30–2:50 Create inspection task
- 2:50–3:00 Impact line + what's next

Record from cached-but-real responses. Do not present mocks as live output. Clearly state what was built during the hackathon.

## 14. Submission checklist

- [ ] Credits applied; spend limit set
- [ ] Repo public, no secrets, README with setup + architecture + "built during hackathon" note
- [ ] Live URL works without local files
- [ ] Demo access key documented in README and submission notes; public sample investigation opens without it
- [ ] Write/analysis endpoints reject requests without the key
- [ ] 3-min video link opens while logged out
- [ ] All participants listed (just you)
- [ ] Track selected: Indie
- [ ] Demo posted on X and link submitted (Most Viral)
- [ ] Open-weights usage documented if you used Spark Open via Baseten/Modal

## 15. Repo layout

```
greenwatch-ai/
├─ frontend/ (src/pages: Dashboard, NewInvestigation, Workspace, Tasks; services/api.ts)
├─ backend/app/ (api/routes, services/*, db/models.py, schemas, main.py) + tests/
├─ scripts/gate0.py
├─ docs/ (architecture.md, data-sources.md, demo-script.md)
├─ .env.example  README.md  LICENSE
```

## 16. Risks and responses

| Risk | Response |
|---|---|
| SAM weak on vegetation | Lead with buildings/trees; report vegetation as experimental |
| No aligned pair | Use pre-registered/orthorectified imagery; show the agent refusing a bad pair as a feature, plus one convincing success |
| API latency/cost | Cache by hash; keep images ≤ 2048 px; check Billing daily |
| Spark Open tool calling flaky | Stay on Muse Spark; open-weights is optional |
| Scope creep | Section 2 is frozen. New ideas go in a "later" list. |
