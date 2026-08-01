# EnMotion control plane

This directory is an independent, lightweight service for a 3–5 person EnMotion
installation. Employee computers keep projects, media, FFmpeg, Demucs, and all
generation work locally. This service contains only:

- opaque account sessions and revocation;
- administrator account controls and audit history;
- integer credit balances, reservations, settlements, refunds, and rate cards;
- a fixed-route provider relay whose credentials never leave the server;
- signed release metadata and short-lived, account-bound update capabilities;
- the static administrator interface at `/admin/`.

It deliberately does not include Postgres, Redis, Celery, media storage, model
weights, FFmpeg, or a frontend build toolchain. Caddy, one Uvicorn worker, and
SQLite WAL fit the intended 1 GB VPS.

## Local development

Use Python 3.11 or newer:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
export ENMOTION_ENV=development
export ENMOTION_DATABASE_URL="sqlite:///$(pwd)/development.db"
export ENMOTION_SESSION_HMAC_SECRET="replace-with-at-least-32-random-characters"
export ENMOTION_PROVIDER_BASE_URL="https://provider.example.com/v1"
export ENMOTION_PROVIDER_CREDENTIALS_JSON='{}'
export ENMOTION_PROVIDER_CONFIG_MASTER_KEY="$(
  python3 -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'
)"
.venv/bin/alembic upgrade head
.venv/bin/python -m app.cli bootstrap-admin --username admin
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8080 --loop asyncio
```

The bootstrap command reads the password without echoing it. Open
`http://127.0.0.1:8080/admin/`. OpenAPI documentation is available at `/docs`
only outside production.

Run verification with:

```bash
.venv/bin/pytest
.venv/bin/python -m app.cli check-ledger
```

`ENMOTION_AUTO_CREATE_SCHEMA=true` exists only for disposable tests. Production
must run `alembic upgrade head`.

The administrator page includes an **API 配置** tab. It manages one
organization-wide provider base URL and model credential set used by every
employee. Existing secrets are never returned to the browser: blank fields keep
the current value, a new value rotates it, and the explicit removal checkbox
disables that model.

## Credit rules

Credits are signed 64-bit-compatible integers. Floating-point currency is never
stored. An upstream submission follows this state machine:

1. A SQLite `BEGIN IMMEDIATE` transaction verifies the active account and rate
   card, subtracts available credits, adds reserved credits, and writes an
   immutable ledger entry.
2. A successful provider acceptance captures the reservation.
3. A connection failure known to precede provider acceptance is retried with
   the same provider idempotency key. An explicit 429 rejection is also retried.
   Exhausted pre-acceptance retries or another provider 4xx release the credit.
4. A read/write timeout, redirect, or provider 5xx has an ambiguous billing outcome, so
   the request becomes `pending_reconciliation` and credits remain reserved.
5. An administrator settles or refunds an ambiguous request after checking the
   provider.

`(user_id, idempotency_key)` is unique. A duplicate with an identical
fingerprint never makes a second provider call or charge. Synchronous image
responses are validated and kept for up to 24 hours in an AES-GCM encrypted,
owner-only replay cache next to the database. A duplicate image request returns
that exact cached result; other duplicates receive HTTP 202 with the existing
usage record. Reusing the key for different content receives 409.

## Provider boundary

The only billable upstream routes are:

- `POST /api/v1/gateway/chat/completions`
- `POST /api/v1/gateway/images/generations`
- `POST /api/v1/gateway/images/edits`
- `POST /api/v1/gateway/video/generations`

Video status and content routes are read-only and ownership-bound. All billable
requests require an `Idempotency-Key`. The service accepts only the seven model
IDs in `app/config.py`, validates each operation/model capability, discards
client authorization, and injects the matching server-side secret. Environment
values from `ENMOTION_PROVIDER_CREDENTIALS_JSON` are the initial/fallback
configuration. After an administrator saves the API configuration, an AES-GCM
encrypted, versioned database record becomes authoritative immediately without
a restart. It never accepts an upstream URL from a client.

`ENMOTION_PROVIDER_CONFIG_MASTER_KEY` must be a dedicated URL-safe base64
encoding of exactly 32 random bytes. Keep it in `/etc/enmotion-control.env` with
mode `0600`; never reuse the session HMAC secret. Provider task records retain
the configuration version used at submission so later status/content requests
continue with the matching credential after rotation.

Chat/video provider responses and verified release files are streamed. Image
generation responses are bounded, validated, and encrypted temporarily so a
desktop disconnect can recover the exact provider result without a duplicate
charge. Expired entries are deleted automatically and are not included in the
SQLite backup. Release archives are first staged in private temporary storage
and checked against manifest size/SHA-256; served downloads are capped globally
and per account. Image-edit uploads may be spooled transiently by the multipart
parser but are never retained by the application. The service does not log
request bodies, provider credentials, authorization headers, cookies, prompts,
or generated media.

See [API contract](docs/API_CONTRACT.md) for desktop-sidecar and administrator
payloads.

## VPS deployment

The production layout assumed by `deploy/` is:

```text
/opt/enmotion-control/             application and virtual environment
/etc/enmotion-control.env         root-owned 0600 configuration and secrets
/etc/enmotion-caddy.env           root-owned 0600 public domain/ACME settings
/etc/enmotion-control/releases.json root:enmotion-control 0640 release manifest
/var/lib/enmotion-control/        SQLite database
/var/backups/enmotion-control/    encrypted online backups
```

1. Create a locked `enmotion-control` system account.
2. Copy this directory to `/opt/enmotion-control`, create `.venv`, and install
   `requirements.txt`.
3. Create the state/backup directories owned by that account.
4. Copy `.env.example` to `/etc/enmotion-control.env`, replace every placeholder,
   make it `0600`, and configure the public HTTPS origin, exact model
   credentials, provider configuration master key, and release-host allowlist.
   Public EnMotion GitHub Release
   assets do not require a GitHub credential.
   Install `releases.json` atomically as `root:enmotion-control` with mode `0640`
   so the unprivileged service can read it without being able to modify it.
   During a control-plane hostname migration, set
   `ENMOTION_PUBLIC_BASE_URL` to the new canonical HTTPS origin and place only
   the explicitly approved former origins in
   `ENMOTION_PUBLIC_BASE_URL_ALIASES`. This keeps updater capability URLs
   same-origin for both desktop generations without trusting arbitrary Host
   headers. Remove aliases after every known installation has upgraded.
5. Install `deploy/enmotion-control.service`, the backup unit/timer, Caddy 2.8 or
   newer, and `age`. Copy `deploy/Caddyfile` to the Caddy configuration. Copy
   `deploy/enmotion-caddy.env.example` to `/etc/enmotion-caddy.env`, set the real
   domain/email with mode `0600`, and install
   `deploy/enmotion-caddy.override.conf` as
   `/etc/systemd/system/caddy.service.d/enmotion.conf`.
6. Run migrations and the one-time administrator bootstrap.
7. Run `systemctl daemon-reload`, then enable the application, Caddy, and
   backup timer.

Keep one Uvicorn worker and force the standard asyncio loop. The approved
provider endpoint is reachable from this Linux host through asyncio, while the
optional uvloop transport can time out during TCP connect. Add 1–2 GB swap on
the 1 GB VPS, expose only SSH/80/443,
use key-only SSH, and build desktop/static artifacts off-server.

The backup job uses SQLite's online backup API and verifies the resulting
database. Managed provider credentials remain encrypted inside that database.
Production refuses plaintext backups unless explicitly overridden. Back up the
provider configuration master key separately in the organization secret store;
the database backup alone cannot decrypt the credentials. Copy encrypted
backups off the VPS and periodically perform a restore drill.

Before each update:

1. create and verify an encrypted backup;
2. install code into a versioned release directory or preserve the prior
   application directory;
3. run `alembic upgrade head`;
4. restart and require `/health/ready` to succeed;
5. verify administrator login, a mock/non-billable gateway call, ledger
   invariants, and the published revision;
6. roll application code back if unhealthy, without overwriting SQLite or
   desktop application-data/output directories.

The API is versioned at `/api/v1` so employee desktop updates can be staggered.

Release manifests use an immutable, versioned public GitHub Release URL:
`https://github.com/paulyan678/EnMotion/releases/download/desktop-vX.Y.Z/ASSET`.
They must never use `/releases/latest/`, a branch URL, query credentials, or a
mutable redirect as the manifest source. The control plane downloads without a
GitHub token, follows only exact allowlisted HTTPS redirects, and verifies the
declared size and SHA-256 before serving the signed package.

Release-session URLs are short-lived bearer capabilities. Production Uvicorn
access logging is disabled, and Caddy skips access logs for those paths so the
capability token is not written to routine web-server logs.
