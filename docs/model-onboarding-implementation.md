# Model catalog and onboarding

EnMotion uses New API as its only AI Provider. The model catalog is the source of truth for supported IDs, capabilities, display metadata, defaults, and model-specific credential names.

## Current contract

| Group | Approved model IDs | Default |
|---|---|---|
| Chat | `deepseek-v4-flash`, `qwen3.7-max`, `deepseek-v4-pro` | `deepseek-v4-flash` |
| Image | `gpt-image-2` | `gpt-image-2` |
| Video | `doubao-seedance-2-0-260128`, `doubao-seedance-2-0-fast-260128`, `doubao-seedance-2-0-mini-260615` | `doubao-seedance-2-0-fast-260128` |

Supported capabilities are `chat`, `t2i`, `i2i`, `t2v`, and `i2v`. Video input is limited to one image. Multi-reference reference-to-video is not part of the implemented contract and must not be advertised.

## Ownership

- `config/model_catalog/catalog.meta.yaml` owns the three active defaults.
- `config/model_catalog/families/*.yaml` owns approved model metadata and each model's exact credential environment variable.
- `src/utils/model_catalog.py` validates source YAML and generates the canonical artifacts.
- `config/model_catalog/generated/model_catalog.json` is the backend artifact.
- `frontend/src/generated/modelCatalog.json` is the frontend artifact.
- `config/model_catalog/schema/model-catalog.schema.json` is generated schema metadata.
- `src/utils/newapi_models.py` owns runtime model-to-key validation and persistence metadata.
- `src/models/newapi.py` owns New API request construction.

Generated JSON must not be edited by hand.

## Required invariants

Every model entry must:

1. Use `provider: newapi`, `supported_backends: [newapi]`, and `default_backend: newapi`.
2. Use one of the three selection groups: `chat`, `image`, or `video`.
3. Declare only capabilities valid for that group.
4. Declare exactly one `credential_sources.newapi` field containing that model's dedicated key name.
5. Declare `runtime.newapi.gateway: newapi`.
6. Be visible only on surfaces where the capability is usable.

Never add an implicit key, model, or Provider fallback. The runtime must reject an unsupported model or a selected model whose dedicated key is absent.

## Adding or changing a model

1. Confirm the exact model ID and supported request contract in New API.
2. Update the appropriate family YAML. Add a new family only when the capability group requires it.
3. Add a dedicated `NEWAPI_<MODEL>_API_KEY` field to `.env.example` and the secure settings allowlist.
4. Update defaults only if the product default intentionally changes.
5. Update selectors and migration logic so stale saved IDs resolve to an approved default with user-visible behavior.
6. Regenerate artifacts with the normal build script.
7. Run validation and tests.

```bash
.venv/bin/python scripts/build_model_catalog.py
.venv/bin/python scripts/validate_model_catalog.py
.venv/bin/pytest -q
cd frontend && npm run typecheck
cd frontend && npm run test:all
cd frontend && npm run build
```

Tests should cover category filtering, exact defaults, model-to-key mapping, persistence, unsupported model rejection, missing-key validation, secret masking, and absence of removed Provider/model options.

## Security

- Keep `.env` ignored and set its permissions to `0600`.
- Never print, log, return, or commit full API keys.
- API responses may expose only configured status and masked placeholders.
- A masked placeholder sent back by the UI means “keep the saved value”; it must never overwrite the stored secret.
- Migration from older shared keys may populate an empty model-specific field only when the mapping is unambiguous. Never overwrite a model-specific key.

See [New API contract](api-reference/newapi.md) for endpoints and the exact model-to-key table.
