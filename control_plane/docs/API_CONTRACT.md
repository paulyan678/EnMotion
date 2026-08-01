# EnMotion control-plane API contract

All timestamps are ISO 8601 UTC values. Desktop sidecars use bearer tokens.
The hosted administrator page uses HttpOnly cookies plus `X-CSRF-Token`.

## Authentication

`POST /api/v1/auth/login`

```json
{
  "username": "employee",
  "password": "at-least-12-characters",
  "device_label": "Paul's MacBook"
}
```

```json
{
  "access_token": "opaque",
  "refresh_token": "opaque",
  "token_type": "bearer",
  "access_expires_at": "2026-07-24T01:00:00Z",
  "refresh_expires_at": "2026-07-31T00:45:00Z",
  "csrf_token": "opaque-non-bearer-token",
  "user": {
    "id": "stable-uuid",
    "username": "employee",
    "role": "user",
    "active": true,
    "available_credits": 100,
    "reserved_credits": 0,
    "created_at": "2026-07-24T00:00:00Z",
    "updated_at": "2026-07-24T00:00:00Z"
  }
}
```

The local sidecar keeps remote tokens out of JavaScript. Persist refresh tokens
only in the OS credential store.

`POST /api/v1/auth/refresh` accepts `{"refresh_token":"opaque"}` and returns the
same shape with both tokens rotated. The hosted administrator may send `{}` and
use its HttpOnly refresh cookie plus CSRF header.

`GET /api/v1/auth/session` and `GET /api/v1/auth/me` return:

```json
{"user": {"id": "..."}, "session": {"id": "...", "device_label": "..."}}
```

The actual objects include all fields described by the OpenAPI schema.

`POST /api/v1/auth/logout` accepts `{"refresh_token":"optional"}`.
`POST /api/v1/auth/change-password` accepts
`{"current_password":"...","new_password":"..."}` and revokes every session.

There is no signup endpoint.

## Account

- `GET /api/v1/account/me` returns the current user.
- `GET /api/v1/account/balance` returns
  `{"available_credits":90,"reserved_credits":10,"total_credits":100}`.
- `GET /api/v1/account/usage?limit=50&cursor=...` returns
  `{"items":[...],"next_cursor":null}`.

Treat the server balance as authoritative. A displayed quote is never admission
authority.

## Administrator

- `GET /api/v1/admin/users`
- `POST /api/v1/admin/users` with
  `{"username":"name","password":"...","role":"user","initial_credits":0}`
- `PATCH /api/v1/admin/users/{id}/status` with `{"active":false}`
- `POST /api/v1/admin/users/{id}/password` with `{"new_password":"..."}`
- `GET /api/v1/admin/users/{id}/sessions`
- `POST /api/v1/admin/users/{id}/sessions/revoke` with
  `{"except_session_id":null}`
- `POST /api/v1/admin/users/{id}/credits` with
  `{"delta":50,"reason":"July refill","idempotency_key":"operator-generated-uuid"}`
- `GET /api/v1/admin/ledger`
- `GET`, `POST /api/v1/admin/rate-cards`
- `PATCH /api/v1/admin/rate-cards/{id}`
- `GET /api/v1/admin/provider-config`
- `PATCH /api/v1/admin/provider-config`
- `POST /api/v1/admin/usage/{id}/settle`
- `POST /api/v1/admin/usage/{id}/refund`
- `GET /api/v1/admin/audit`

Credit adjustment keys must be unique and are safely replayable. Passwords and
provider secrets never appear in audit details.

`GET /api/v1/admin/provider-config` returns only configuration metadata:

```json
{
  "version": 3,
  "source": "managed",
  "base_url": "https://provider.example.com/v1",
  "writable": true,
  "updated_at": "2026-07-25T03:00:00Z",
  "models": [
    {"model": "deepseek-v4-flash", "capability": "chat", "configured": true}
  ]
}
```

It never returns a credential value, suffix, ciphertext, or nonce. To rotate or
remove selected credentials, send only the intended changes:

```json
{
  "base_url": "https://provider.example.com/v1",
  "credentials": {
    "deepseek-v4-flash": "new-secret-value",
    "qwen3.7-max": null
  }
}
```

An omitted model retains its current credential; `null` explicitly removes it.
Cookie-authenticated mutations require the normal CSRF header. Each save creates
an encrypted immutable version and audits only the changed field names and
model IDs.

## Provider gateway

Billable POSTs require:

```http
Authorization: Bearer <access token>
Idempotency-Key: <8-128 safe characters>
```

Routes mirror the supported provider surface under `/api/v1/gateway`. For
synchronous image generation and image editing, a duplicate completed request
within the 24-hour recovery window returns the exact encrypted cached response
with `X-EnMotion-Idempotent-Replay: true`. Other duplicate completed requests
return HTTP 202:

```json
{
  "idempotent_replay": true,
  "usage_request": {
    "id": "...",
    "status": "settled",
    "reserved_units": 7,
    "settled_units": 7
  }
}
```

This prevents a second provider call or charge. The desktop preserves every
successful response locally. The control plane retains no prompt, and retains
only the encrypted, bounded image response needed for short-lived recovery.

## Runtime and updates

`GET /api/v1/runtime-config` is public and returns stable route bases and the
maximum gateway body size.

The desktop starts an update check with an authenticated request:

```http
POST /api/v1/releases/session
Authorization: Bearer <employee-access-token>
Content-Type: application/json

{
  "target": "darwin",
  "arch": "aarch64",
  "current_version": "1.0.0",
  "channel": "stable"
}
```

The response contains one HTTPS, same-origin capability URL:

```json
{
  "manifest_url": "https://accounts.example.com/api/v1/releases/session/opaque-token/manifest"
}
```

The raw token is never stored. Its HMAC digest is bound to the employee,
platform, channel, current version, selected release, and expiry. Capability
paths intentionally require no cookie or header because the Tauri updater
consumes them directly. They re-check that the owning employee remains active.
The manifest returns `204` when the installed version is current, otherwise it
returns Tauri's dynamic signed-update shape:

```json
{
  "version": "1.2.3",
  "url": "https://accounts.example.com/api/v1/releases/session/opaque-token/download",
  "signature": "Tauri Minisign signature",
  "notes": "Release notes",
  "pub_date": "2026-07-24T00:00:00Z"
}
```

The legacy authenticated
`GET /api/v1/releases/latest?platform=macos-arm64&channel=stable&current_version=1.0.0`
returns the fuller internal metadata shape but never the upstream source URL:

```json
{
  "version": "1.2.3",
  "platform": "macos-arm64",
  "channel": "stable",
  "sha256": "64-hex-characters",
  "size_bytes": 123,
  "published_at": "2026-07-24T00:00:00Z",
  "signature": "Tauri Minisign signature",
  "minimum_supported_version": "1.0.0",
  "notes": "Release notes",
  "download_url": "/api/v1/releases/macos-arm64/1.2.3/download?channel=stable"
}
```

The authenticated download route first stages the upstream archive in a private
temporary file and verifies its size and SHA-256. The desktop updater must also
verify size, SHA-256, and the Tauri signature before installation, then
atomically replace only application binaries. It must never remove or overwrite
EnMotion application-data output, settings, or credential entries.

For the public EnMotion repository, publish CI writes the immutable,
version-specific GitHub URL
`https://github.com/paulyan678/EnMotion/releases/download/desktop-vX.Y.Z/ASSET`
into the server manifest. `/releases/latest/`, branch URLs, query credentials,
and URLs from a different repository are forbidden. The control plane sends no
GitHub credential and follows a maximum of three exact-allowlisted HTTPS
redirects before applying size and SHA-256 verification.
