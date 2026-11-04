# 3-minute demo script

Rules from the hackathon: the video is a primary judging artifact, must show only what was built during the event, and must use **real model responses**. Never stage a failure, and never present a recording of a mock as live output. Replaying cached-but-real responses is fine (the SAM cache makes re-runs instant and free).

Judging weights to design around: Impact 30%, Use of Meta AI tooling 25%, Demo 25%, Technical depth 20%. The current production design uses Gemini for agent orchestration and vision QC, with Meta SAM for segmentation. Verify with the organizers that this provider split satisfies the Meta-tooling criterion; do not describe Gemini as Muse Spark.

| Time | Show | Say (adapt to your own words) |
|---|---|---|
| 0:00-0:20 | The two real images of your site, side by side | The problem in one sentence: someone has to find out whether a site changed between two dates, measure it, document it and decide whether to send an inspector. Today that is manual and scattered. |
| 0:20-0:40 | New investigation form: upload both images, enter dates and source, tick categories | GreenWatch turns two images into an evidence-backed investigation. |
| 0:40-1:30 | Start the run. Live agent steps; SAM overlays appearing; the quality check confirming (or, if it really happens, rejecting and retrying) a mask | Meta SAM 3.1 finds the regions. Gemini runs the investigation: it calls real tools, reviews the segmentation, and the backend enforces the rules. If the first mask is poor, the agent retries with a different prompt, and flags it for a human if it still is not trustworthy. |
| 1:30-2:00 | Results panel: detected-pixel counts, change, the "requires human review" wording, the limitations drawer | Every number is computed in code, not by the model. Without georeferencing it reports pixels, never square metres, and never claims anything illegal. |
| 2:00-2:30 | Open the PDF report | An evidence-linked report. A failed run produces a diagnostic document that cannot be mistaken for a finding. |
| 2:30-2:50 | Approve an inspection task | A person approves, and the finding turns into a follow-up. |
| 2:50-3:00 | Closing line | Who benefits, and what is next (satellite ingestion, field photos, multi-user). State plainly what was built during the hackathon. |

## Before recording

- [ ] Gate 0 passed for Gemini and Meta SAM, and you have looked at the overlays: they are plausible.
- [ ] The run you record is a real run on your real, licensed imagery.
- [ ] `/health` hit on the deployed backend (free-tier cold start).
- [ ] Browser zoom and window size set for 1080p; notifications off; no secrets (demo key, API keys) on screen. Enter the key before recording and hide the field.
- [ ] Gemini and Meta billing: spend limits set; credits not exhausted.
- [ ] Rehearse once with the cache warm so timing is predictable.

## After recording

- [ ] Video link opens while logged out.
- [ ] Post the demo on X and submit the post link on the platform (Most Viral prize counts engagement until Nov 8).
- [ ] Put the demo key in the submission notes so judges can run a live analysis; the public sample opens without it.
