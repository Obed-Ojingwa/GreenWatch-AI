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

## OpenAerialMap research leads (not used in the demo or sample)

The current map is [imagery.hotosm.org](https://imagery.hotosm.org/?z=15&lat=6.4978&lng=3.3909), centered near Makoko, Lagos. Catalog leads found during research:

- “Makoko” by Uhurulabs, captured 2019-10-02; catalog bounds 3.384854, 6.490230 to 3.397062, 6.505367; listed GSD about 0.054 m.
- “Maxar 2019 Lagos Mosaic” by Maxar, captured 2019-06-05; catalog bounds 3.080997, 6.350414 to 3.748394, 6.869687; listed GSD 0.5 m and overlaps the Makoko bounds.

These are leads only, not a validated before/after pair. The catalog item license fields were blank. [OAM's legal page](https://openaerialmap.org/legal) describes CC BY 4.0 for OIN imagery but preserves original contributors' copyright; verify each item's rights, attribution, dates, and visible change before downloading, using, or publishing it. Neither image has been downloaded or used in the project.
