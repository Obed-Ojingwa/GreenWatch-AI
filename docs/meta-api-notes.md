# Production AI provider setup

The production default is `AI_PROVIDER=gemini`: Gemini handles agent/tool orchestration,
interpretations, and vision QC. Create `GEMINI_API_KEY` in [Google AI Studio](https://aistudio.google.com/apikey)
and store it only in local ignored `backend/.env` or the Render backend environment. Set `AGENT_MODEL`
to a model ID currently available to that key; `VISION_QC_MODEL` may override it for QC. The adapter
uses Google's [OpenAI-compatible Chat Completions API](https://ai.google.dev/gemini-api/docs/openai)
for function calling and image inputs. It does not send `response_format`/`json_schema`; QC asks for
JSON and validates the response locally, failing closed if it cannot parse it.

Meta remains a distinct provider for SAM pixel-mask segmentation. Set `META_API_KEY`,
`META_BASE_URL`, `META_SEGMENT_PATH`, and `SAM_MODEL` for that role. A working Gemini chat call does
not prove SAM access, and vice versa. Current successful provider runs must be attributed accurately;
do not describe Gemini output as Muse Spark or Meta output.

The Meta key belongs in local ignored `backend/.env` or Render's backend environment only. The Gemini
key also stays server-side; neither provider key belongs in Vercel, `VITE_*`, source control, or chat.
Set a separate `META_DIAGNOSTIC_KEY` to protect the sanitized `/api/ai/diagnostics` endpoint. That
diagnostic makes a minimal billable chat call; Gemini model catalog listing is not assumed.

## Meta Model API: documented contract and Gate 0 results

The request shapes below reflect Meta's published API contract, not a successful request from this project's account. Run Gate 0 and record only sanitized account-specific results. Never paste API keys, raw mask output, signed URLs, or private image data into this file.

Gate 0 date: ____  Pair description (no private filenames): ____  Overall result: ____

## Access

| Item | Value | Setting |
|---|---|---|
| Base URL | `https://api.meta.ai/v1` (documented; account access not tested) | `META_BASE_URL` |
| Auth header name | `Authorization` (documented) | `META_AUTH_HEADER` |
| Auth scheme | `Bearer` (documented) | `META_AUTH_SCHEME` |
| Credits visible under Billing -> free credits? | | n/a |
| Spend-limit alert set? | | n/a |
| Rate limits / quotas seen | | n/a |

## Muse Spark (chat, vision, tools)

| Item | Value | Setting |
|---|---|---|
| Chat path | `chat/completions` (documented OpenAI-compatible API) | `META_CHAT_PATH` |
| Model id for the agent | `muse-spark-1.3` (verify access) | `AGENT_MODEL` |
| Model id for vision QC (blank = same as agent) | | `VISION_QC_MODEL` |
| Request format matches OpenAI-style chat completions? (messages, `tools`, `tool_calls`, `response_format`, `image_url` parts) | | code: `HttpMetaClient.chat` / `_parse_chat` |
| Image input format that worked | | |
| Tool calling works? Quirks (parallel calls, argument encoding, `finish_reason`) | | |
| Structured output (`json_schema`) works? `strict` supported? | | |
| Typical latency per call / per image | | |
| Approx. cost per call | | |

### `model_not_found` troubleshooting

The public model IDs are valid request identifiers; that alone does not establish that the API key's
account can use them. The backend reports the endpoint and model identifier for each provider error:

- `chat/completions` with `AGENT_MODEL` (or `VISION_QC_MODEL`): verify the Muse Spark model ID and
  that the API key/account has Model API access.
- `responses` with `SAM_MODEL`: verify SAM access separately; a successful Muse Spark call does not
  prove SAM access.

The app's `META_API_KEY` setting is sent as `Authorization: Bearer ...`; the environment variable
name is app-specific. Keep the key only in Render's backend environment, never in Vercel or chat.
Use Gate 0 to test the account; do not share keys, image contents, or raw provider payloads.

Official references: [Quickstart](https://dev.meta.ai/docs/quickstart),
[Chat Completions](https://dev.meta.ai/docs/protocols/chat-completions), and
[Media segmentation](https://dev.meta.ai/docs/media-segmentation). Meta's
[API reference](https://dev.meta.ai/docs/api-reference) documents `GET /v1/models`.

When diagnosing a production `model_not_found`, use the protected `/api/meta/diagnostics`
route to compare the configured model with the models returned by `GET /models` using the
running service's key. If the requested model is absent from a successful catalog response,
the running key does not list that model. If it is listed but chat still returns
`model_not_found`, the failure is specific to the chat request/model availability and should be
escalated to Meta with the sanitized model ID, endpoint, and error code. A local-key success
does not establish which key or project Render is using.

## SAM 3.1 (segmentation)

| Item | Value | Setting |
|---|---|---|
| Endpoint path | `responses` (documented) | `META_SEGMENT_PATH` |
| Model id | `sam-3.1` (verify access) | `SAM_MODEL` |
| Request | `stream: true`; one user message containing `input_text` and a PNG `input_image` data URI | implemented in `app/services/sam_responses.py` |
| Mask output | `response.output_text.delta` SSE events, terminated by `response.completed` | implemented in `app/services/sam_responses.py` |
| Mask encoding | Responses metadata `mask_encoding: one_bit`; special-token records decoded with Meta's public `meta-sam-parser` API | `META_SAM_MASK_ENCODING` |
| JSON body template and mask path | Not used in Responses mode; only required by the legacy JSON adapter | `META_SEGMENT_BODY`, `META_SEGMENT_MASKS_PATH` |
| Max image size / dimensions accepted | | `MAX_IMAGE_DIM` |
| What an empty detection looks like | Completed output with no segmentation records |
| Latency per image | | |
| Prompts that worked well on your imagery (`buildings`, `trees`, `vegetation`, ...) | | |
| Prompts that did not | | |

## Muse Spark Open / open weights (optional bonus)

| Item | Value |
|---|---|
| Provider (Baseten / Modal) and credits granted? | |
| Base URL and key (per-model override is not currently implemented) | |
| Model id (hackathon guide says `meta-models/muse-spark-open-1026` on Baseten) | |
| Tool calling reliable enough for the agent loop? | |

## Findings that changed the code

(List any adapter change you had to make, with the date.)
