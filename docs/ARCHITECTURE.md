# EnMotion architecture

## Trust boundary

```text
EnMotion desktop
  Tauri window
       |
       | same-origin HTTP + HttpOnly local session + CSRF + launch nonce
       v
  Python/FastAPI sidecar on a random loopback port
       |
       | HTTPS + opaque employee access token
       v
EnMotion control plane
  auth / sessions / credits / rate cards / audit / update gateway
       |
       | server-held provider credential
       v
Allowlisted AI provider
```

The local sidecar owns media and project operations. The control plane owns
identity and company-funded operations. It never runs media processing and does
not retain generated media.

## Desktop components

### Tauri shell

- Launches the packaged Python sidecar with a random port and nonce.
- Waits for a bounded health check before showing the application.
- Shows a subtle update indicator only when a signed release is available.
- Downloads in the background, asks the sidecar to flush durable state, installs,
  and relaunches.
- Never stores a repository credential or provider credential.

### Static frontend

- Uses the sidecar's loopback origin.
- Receives only local runtime configuration.
- Keeps the remote refresh token out of JavaScript and browser storage.
- Scopes browser state by the immutable account/workspace identifier.

### Python sidecar

- Binds only to loopback.
- Uses an HttpOnly local session, CSRF token, and per-launch nonce.
- Keeps access tokens in memory.
- Keeps a rotated refresh token in the OS credential store.
- Resolves account workspaces beneath `Documents/enmotion-output`.
- Routes provider calls through the control plane in managed mode.
- Fails closed when a billable operation cannot be authorized.

## Control plane

The control plane is sized for 3–5 people:

- One Uvicorn worker
- SQLite with WAL, foreign keys, and a busy timeout
- Static admin assets
- Caddy TLS termination
- One systemd service
- Encrypted off-host backups

It intentionally excludes PostgreSQL, Redis, Celery, Node.js at runtime, Torch,
Demucs, FFmpeg, and media volumes.

### Credits

All monetary-like values use integer credit units. A billable request follows:

1. Authenticate and validate a fixed operation/model.
2. Resolve a versioned server-side rate.
3. Create an atomic reservation.
4. Submit with a unique idempotency key.
5. Settle the reservation on confirmed success.
6. Refund a known pre-submission failure.
7. Retain an ambiguous result for reconciliation.

Ledger entries are immutable. Administrative adjustments record an actor and a
reason instead of rewriting history.

## Local storage

```text
Documents/
  enmotion-output/
    accounts/
      <stable-account-id>/
        output/
          assets/
          video/
          export/
          uploads/
          thumbnails/
```

Application data is separate:

- macOS: `~/Library/Application Support/EnMotion`
- Windows: `%LOCALAPPDATA%\EnMotion`

The installer and updater replace application binaries only.

## Release security

- Semantic versions are immutable.
- macOS artifacts require Developer ID signing, hardened runtime,
  notarization, and stapling for production.
- Windows artifacts require Authenticode signing and timestamping.
- The updater pins a public key and rejects invalid signatures, wrong
  architectures, and downgrades.
- Private release downloads are mediated by the authenticated control plane;
  the repository credential remains server-side.
