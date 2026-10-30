# What is left (read this first)

Status as of **Fri Oct 30, 2026 (Lagos)**. Real deadline: **Tue Nov 3, night** (platform deadline is Nov 4, 9:00 PM ET = Nov 5, 3:00 AM Lagos).

## The one-line state

The application is built and passes its own tests, but **nothing has ever talked to the real Meta API**. Everything Meta-specific is configuration waiting for Gate 0. Until Gate 0 passes on your credentials and your imagery, you have a well-tested app, not a working submission.

## What was checked, and how

| Check | Result | Caveat |
|---|---|---|
| Backend test suite (SQLite) | 101 passed | Meta is faked in tests (a scripted fake and a local mock HTTP server). Proves our adapter is self-consistent, not that Meta behaves the same. |
| Backend suite on real PostgreSQL 16 | 99 passed; 2 failed only because of how I ran them (a scratch copy without the `frontend/` folder; a config test that expects the SQLite default) | Both pass in the repo as shipped. |
| `schema.sql` on PostgreSQL 16 | Applies cleanly (7 tables) | Not yet applied to a Supabase project. |
| Frontend typecheck and production build | Pass | |
| Frontend in a real browser (Chromium) | Dashboard, new-investigation form and workspace render; no JavaScript errors; no horizontal scroll at 390 px | Checked with **synthetic** dev data. Google Fonts were blocked in my sandbox, so typography fell back. |
| `scripts/gate0.py` | Probe, SAM, overlay, vision review, tool-call and alignment checks all run and report correctly | Only against local stand-in servers. |
| `scripts/seed_sample.py` | Drove the real HTTP routes end to end and published a sample | Only against a local stand-in server. |

## Blockers only you can clear (in this order)

1. **Gate 0, today.** Put your Meta credentials and the documented request/response shapes into `backend/.env`, then:
   - `cd backend && python ../scripts/gate0.py probe --image YOUR_IMAGE.jpg` prints the raw SAM response and test-decodes candidates for `META_SEGMENT_MASKS_PATH`.
   - `python ../scripts/gate0.py run --earlier A.jpg --newer B.jpg` runs the full checklist. Look at `gate0_out/overlay_*.png` yourself: a green run with nonsense masks is not a pass.
   - Copy what you learn into `docs/meta-api-notes.md`.
   - If Meta's wire format differs from the OpenAI-style assumption for chat or tool calls, `HttpMetaClient._parse_chat` and `chat()` in `backend/app/services/meta_client.py` are the places to change. Segmentation should need only `.env` changes.
2. **Imagery.** One before/after pair of the same site with a visible change and **rights to publish it** (your own drone data, or openly licensed). Fill in `docs/data-sources.md`. No Google Earth screenshots.
3. **Credits.** Confirm the $150 appears under Billing, set the spend-limit alert, and request the Baseten and Modal credits if you want the open-weights bonus.
4. **Tune on real imagery.** QC coverage bounds (`qc_bounds` in `app/core/config.py`) and alignment thresholds are placeholders. Adjust them from what Gate 0 shows, and decide whether buildings or trees lead the demo.
5. **Supabase.** Create the project, apply `backend/app/db/schema.sql` in the SQL editor, create a **private** bucket named `greenwatch`.
6. **Deploy.** Backend on Render (`render.yaml` is provided), frontend on Vercel (root `frontend/`, set `VITE_API_BASE_URL`). In production the backend refuses to start unless the demo key is 16+ characters, the database is Postgres, storage is Supabase and the CORS origin is not localhost.
7. **Seed the public sample** from a real run: `scripts/seed_sample.py` (usage at the top of the file). Check it by eye.
8. **Demo video** (3 minutes, real responses only; see `docs/demo-script.md`), **X post**, and **submission** on the Cerebral Valley platform. Add the demo key to the submission notes.
9. **Commit.** The repo has no commits. Commit a baseline now and commit daily: the history is your evidence that this was built during the event.

## Known gaps and risks (not yet addressed in code)

- **Open-weights bonus ($10K) is not wired up.** `META_BASE_URL` and `META_API_KEY` are global, but Muse Spark Open runs on Baseten (a different host and key). Using it for the agent needs a per-model base URL/key override in `Settings` and `HttpMetaClient`. About an hour of work; only worth doing after the main path passes Gate 0.
- **Agent behaviour with real Muse Spark is unproven.** The tool-calling loop and system prompt were exercised only against a scripted fake. Real models may call tools in a different order, repeat calls or ignore the schema. The backend enforces prerequisites and caps calls (`MAX_TOOL_CALLS_PER_RUN`), but expect to tune the prompt in `app/services/agent.py`.
- **`SupabaseStorage` has not run against a real Supabase project.** It is a small REST wrapper with no test. Do a manual upload, signed-URL and download round trip right after creating the bucket.
- **GeoTIFF georeference parsing** (`app/services/georef.py`) is covered by tests on GeoTIFFs generated in the test suite (including a feet-based CRS that must not be treated as metres), not by a real-world file. It deliberately returns "no area available" when anything is ambiguous, so the safe failure is no square-metre claim. Treat any square-metre output as unverified until you have checked one real file.
- **Background tasks die on a restart.** A startup hook marks runs stuck in `processing` as `failed`, but a Render restart mid-analysis still loses that run. Free-tier cold starts also add delay: hit `/health` before recording.
- **Rate limiting** is the `slowapi` limit on starting a run (10/minute per IP) plus an hourly global cap (`MAX_AGENT_RUNS_PER_HOUR`). The demo key is the real protection for your card.
- **`docs/RECOVERY_NOTES.md`** describes files that were overwritten early in this build and then rebuilt. The rebuilt modules pass the full test suite, but they are reconstructions, not the originals. If you find an earlier copy of `comparison.py`, `quality.py` or `errors.py`, diff it against the current ones.
- **No automated frontend tests.** Only the typecheck/build and the manual browser check above.

## Not built on purpose (out of scope for the deadline)

User accounts and roles, an interactive map, NDVI/multispectral analysis, satellite ingestion, video analysis, notifications, Alembic migrations, Docker.

## Suggested schedule (Lagos time)

| Day | Goal |
|---|---|
| Fri Oct 30 | Gate 0 passes on real credentials; imagery chosen and licensed; first commit |
| Sat Oct 31 | Tune QC/alignment on real imagery; one clean real run through the UI locally |
| Sun Nov 1 | Supabase + Render + Vercel live; seed the public sample from a real run |
| Mon Nov 2 | Production end-to-end test from a fresh browser; rehearse the demo; optionally try Spark Open |
| Tue Nov 3 | Record and edit the video, finish the README, post on X, **submit** |
| Wed Nov 4 | Buffer only. Do not plan work here. |

If you are behind, cut in this order: task polish, history polish, overlay polish. Never cut: real overlays, the agent loop with QC, the PDF report, the deployed demo.
