# New API contract

## Connection

EnMotion sends every AI request to the shared `NEWAPI_BASE_URL`. The configured value should be the gateway root, normally ending in `/v1`.

Authentication uses a Bearer token selected from the exact requested model:

```http
Authorization: Bearer <matching model key>
```

The backend must validate the model before sending a request. It must not reuse another model's credential, substitute a model, or fall back to another Provider.

## Model and credential mapping

| Capability | Exact model ID | Credential field |
|---|---|---|
| Image | `gpt-image-2` | `NEWAPI_GPT_IMAGE_2_API_KEY` |
| Video | `doubao-seedance-2-0-260128` | `NEWAPI_SEEDANCE_2_API_KEY` |
| Video | `doubao-seedance-2-0-fast-260128` | `NEWAPI_SEEDANCE_2_FAST_API_KEY` |
| Video | `doubao-seedance-2-0-mini-260615` | `NEWAPI_SEEDANCE_2_MINI_API_KEY` |
| Chat | `deepseek-v4-flash` | `NEWAPI_DEEPSEEK_V4_FLASH_API_KEY` |
| Chat | `qwen3.7-max` | `NEWAPI_QWEN_37_MAX_API_KEY` |
| Chat | `deepseek-v4-pro` | `NEWAPI_DEEPSEEK_V4_PRO_API_KEY` |

## Endpoints

All paths below are relative to `NEWAPI_BASE_URL`.

| Operation | Method and path | Models |
|---|---|---|
| Chat completion | `POST /chat/completions` | Three chat models |
| Text-to-image | `POST /images/generations` | `gpt-image-2` |
| Image editing | `POST /images/edits` | `gpt-image-2` |
| Video submission | `POST /video/generations` | Three Seedance models |
| Video status | `GET /video/generations/{task_id}` | Submitted Seedance task |

Chat and image-generation responses are handled synchronously. Video generation returns a task identifier and is polled until the task succeeds or fails.

## Capability matrix

| Model group | Chat | T2I | I2I | T2V | Single-image I2V | Multi-reference R2V |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| Chat models | Yes | No | No | No | No | No |
| `gpt-image-2` | No | Yes | Yes | No | No | No |
| Seedance models | No | No | No | Yes | Yes | No |

The current video request contract accepts at most one reference image. EnMotion therefore does not expose a reference-to-video selector or claim multi-reference support.

## Configuration and secret handling

- The frontend receives only configured status and masked values for saved keys.
- Full saved keys are never returned in API responses.
- Logs must not contain authorization headers or full configuration payloads.
- A selected model without its own configured key fails validation before submission.
- Unsupported model IDs fail validation; no request is sent.
- `.env` remains Git-ignored and should have `0600` permissions.

Active selections are persisted independently in `NEWAPI_CHAT_MODEL`, `NEWAPI_IMAGE_MODEL`, and `NEWAPI_VIDEO_MODEL` (or their equivalent application configuration fields). Changing the chat or video selection takes effect on the next request without restarting the application.
