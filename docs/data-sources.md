# Imagery and data sources

The hackathon bans "code, data, or assets you do not have the rights to", and the repo and demo are public. Every image used for the demo, the public sample or the repo must be listed here with its licence. **Do not use Google Earth / Google Maps screenshots.**

## Imagery pair used for the demo

| Field | Earlier image | Newer image |
|---|---|---|
| File name (not committed unless licence allows) | | |
| Site / location | | |
| Capture date | | |
| Source (own drone, OpenAerialMap, other) | | |
| Licence / permission to publish | | |
| Attribution text | | |
| Resolution (ground sampling distance) | | |
| Georeferenced? CRS? | | |
| Format and size | | |

What visibly changed between the two (one sentence, in plain words): ____

How alignment was achieved (already orthorectified, or the app's ORB + RANSAC alignment): ____

Result of `scripts/gate0.py run` on this pair (date, pass/fail, link to notes): ____

## Choosing a pair

- Same site, different dates, a **visible** change (new buildings, cleared vegetation).
- Prefer already-orthorectified imagery: it makes the comparison trustworthy and keeps the alignment check green.
- High enough resolution to see individual buildings or tree crowns. Sentinel-2 (10 m) is too coarse for that; use it only for broad-area vegetation if nothing else works, and narrow the claims accordingly.
- Candidate sources: your own drone/Pix4D outputs; OpenAerialMap (check each image's licence); other openly licensed aerial imagery.
- If the best pair you can legally use is not in Lagos, say so honestly in the demo. The product is the workflow.

## Synthetic data

`scripts/dev_fake_run.py` and the test suite generate computer-made images with a scripted fake model. They are for development only and must never appear in the demo video, the public sample or the submission. Everything it creates is titled `[SYNTHETIC DEV DATA]`.
