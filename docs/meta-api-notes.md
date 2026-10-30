# Meta Model API: verified facts

**Nothing below is verified yet.** Fill this in during Gate 0 (`scripts/gate0.py probe` and `run`) and keep it current. Every value here maps to a setting in `backend/.env`. Never paste API keys into this file.

Date verified: ____  Verified by: ____

## Access

| Item | Value | Setting |
|---|---|---|
| Base URL | | `META_BASE_URL` |
| Auth header name | | `META_AUTH_HEADER` |
| Auth scheme (`Bearer`, empty for raw key, ...) | | `META_AUTH_SCHEME` |
| Credits visible under Billing -> free credits? | | n/a |
| Spend-limit alert set? | | n/a |
| Rate limits / quotas seen | | n/a |

## Muse Spark (chat, vision, tools)

| Item | Value | Setting |
|---|---|---|
| Chat path | | `META_CHAT_PATH` |
| Model id for the agent | | `AGENT_MODEL` |
| Model id for vision QC (blank = same as agent) | | `VISION_QC_MODEL` |
| Request format matches OpenAI-style chat completions? (messages, `tools`, `tool_calls`, `response_format`, `image_url` parts) | | code: `HttpMetaClient.chat` / `_parse_chat` |
| Image input format that worked | | |
| Tool calling works? Quirks (parallel calls, argument encoding, `finish_reason`) | | |
| Structured output (`json_schema`) works? `strict` supported? | | |
| Typical latency per call / per image | | |
| Approx. cost per call | | |

## SAM 3.1 (segmentation)

| Item | Value | Setting |
|---|---|---|
| Endpoint path | | `META_SEGMENT_PATH` |
| Model id | | `SAM_MODEL` |
| Request body template (placeholders: `{{model}}`, `{{prompt}}`, `{{image_b64}}`, `{{image_data_uri}}`) | | `META_SEGMENT_BODY` |
| Where the masks are in the response (dotted path; `*` fans out over a list) | | `META_SEGMENT_MASKS_PATH` |
| Mask encoding (base64 PNG / COCO RLE / polygon / raw) | | decoded by `app/services/mask_codec.py` |
| Max image size / dimensions accepted | | `MAX_IMAGE_DIM` |
| What an empty detection looks like | | |
| Latency per image | | |
| Prompts that worked well on your imagery (`buildings`, `trees`, `vegetation`, ...) | | |
| Prompts that did not | | |

## Muse Spark Open / open weights (optional bonus)

| Item | Value |
|---|---|
| Provider (Baseten / Modal) and credits granted? | |
| Base URL and key (needs a per-model override; see `docs/REMAINING.md`) | |
| Model id (hackathon guide says `meta-models/muse-spark-open-1026` on Baseten) | |
| Tool calling reliable enough for the agent loop? | |

## Findings that changed the code

(List any adapter change you had to make, with the date.)
