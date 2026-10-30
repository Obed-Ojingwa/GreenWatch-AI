# Recovery notes: files overwritten on 2026-10-29

## What happened
At the start of the "build phases 1-10" session, `backend/` already contained work from an earlier session
(about 3,300 lines, 89 tests, no git commits). The session did not check the folder first and wrote new
versions of five files over the originals. There was no git history to restore from.

## Files affected
| File | What I did | Confidence |
|---|---|---|
| `backend/app/services/comparison.py` | Rebuilt from the compiled original (`__pycache__/comparison.cpython-313.pyc`, written 2026-10-28 21:45) by disassembling every function: rounding, direction rules, overlay colours, `Georef`/`m2_supported` semantics. | High. Logic recovered instruction by instruction. Comments and docstring wording may differ. |
| `backend/app/services/quality.py` | Rebuilt from the compiled original (`quality.cpython-313.pyc`, 21:48). `mask_stats`, `decide`, `_parse_json`, `vision_review`, `QCDecision` signatures and branching recovered. | High for logic. `VISION_SCHEMA` key set and the cap of 5 issues are inferred from constants. |
| `backend/app/core/errors.py` | Rebuilt from usage across the code base (class names, `status_code`, `.detail`, `MetaApiError(msg, status)`). The original bytecode was lost (it was recompiled before the problem was noticed). | Medium. Class set and HTTP codes match every call site and API test, but the original may have had extra helpers. |
| `backend/tests/test_comparison.py` | Rewritten. The 13 original test names were recovered from `.pytest_cache`; bodies are new. | Medium. Same names and intent, different assertions. |
| `backend/pytest.ini` | Rewritten with `testpaths = tests` and `pythonpath = .`. | Medium. May have contained more options. |

`docs/BUILD_PLAN.md` and `docs/blueprint-v3.md` were also copied into `docs/` at the start of the session. If a
newer `docs/BUILD_PLAN.md` existed it was replaced by the copy of the plan delivered in chat; no status edits from
before that point can be confirmed.

## How it was checked
- The earlier session's tests exercise these modules through the agent, API, report and validation paths:
  `test_agent.py`, `test_api.py`, `test_validation.py`, `test_meta_client.py` and others. With the reconstruction
  in place the whole backend suite passes (see `docs/REMAINING.md` for the current count).
- One unrelated test (`test_schema_sql_matches_sqlalchemy_models`) was already failing in the earlier session's
  pytest cache. Cause: its regex `[a-z_]+` could not match the column name `sha256`. Fixed in the test only; the
  schema was already correct.

## What still deserves a human look
1. Diff `errors.py` against your memory of the original if you remember extra exception types.
2. Skim `quality.py` `VISION_SCHEMA` against what the Meta structured-output feature accepts once Gate 0 runs.
3. Put the project under git and commit now, so this cannot recur: `git add -A && git commit -m "Baseline"`.
