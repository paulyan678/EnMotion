# EnMotion desktop update contract

Updates are opt-in and nonblocking. The application may check in the
background, but only an explicit user action downloads a package, and only a
second explicit action installs and restarts.

## State and event contract

Every updater command returns the complete current state and all later changes
are emitted as `enmotion://update-state`.

```text
idle -> checking -> available -> downloading -> ready -> installing
                   \------------------------------------------> error
```

The serialized object contains `status`, `currentVersion`, and optional
`availableVersion`, `releaseNotes`, `progress`, and `error`. Download progress
contains `downloadedBytes` and optional `totalBytes`.

Downloaded bytes are verified by the Tauri updater's embedded public key before
being written to a private cache file. The cache contains no account token and
may be deleted safely. The user's settings, projects, and generated media are
outside the application bundle and update cache.

## Employee-scoped release session

The updater never trusts an unauthenticated public release feed and never
receives the employee's control-plane bearer. Rust reads the HttpOnly local
login cookie from the native webview store and sends it only to the
nonce-authenticated loopback sidecar.
The sidecar refreshes the remote login and calls:

```http
POST /api/v1/releases/session
Authorization: Bearer <employee access token>
Content-Type: application/json

{"target":"darwin","arch":"aarch64","current_version":"1.0.0","channel":"stable"}
```

The control plane verifies the employee is active and returns an opaque,
short-lived capability:

```json
{
  "manifest_url": "https://accounts.example/api/v1/releases/session/<token>/manifest"
}
```

The manifest capability returns `204` when no newer release exists, otherwise
the Tauri dynamic schema:

```json
{
  "version": "1.1.0",
  "url": "https://accounts.example/api/v1/releases/session/<token>/download",
  "signature": "<contents of the Tauri .sig file>",
  "notes": "Release notes",
  "pub_date": "2026-07-24T00:00:00Z"
}
```

The grant must be target-bound, stored only as a hash or be server-signed,
expire quickly, and authorize only its manifest and archive. The download grant
must remain valid long enough for a slow company connection and be revoked
after successful transfer. Capability paths must be redacted from access logs.
Rust rejects non-HTTPS URLs, credentials or query strings, a different origin,
an unexpected path, or a malformed token. The control plane fetches the
versioned public GitHub Release asset without a GitHub credential and verifies
the inventory's SHA-256 and size. Tauri then independently verifies the
embedded Minisign signature. A fresh session is issued immediately before
download so a stale login cannot authorize a package. Manifest checks use a
short total timeout;
archive transfers allow up to six hours but fail after 90 seconds without any
new bytes, so slow company routes do not make the interface freeze or hang
forever.

## Prepare, install, and launch

Before installation, Rust calls the nonce-authenticated
`/_desktop/prepare-update` endpoint. The sidecar:

1. rejects the request while a Comic workspace or Playground generation,
   retry, or export job is active;
2. atomically blocks new mutating local API requests while the installer owns
   the application; a failed install removes this barrier immediately;
3. flushes project, series, library, Playground history, and prompt-template
   metadata under the application's locks;
4. copies only global and per-account workspace metadata files into a private
   staging directory;
5. atomically publishes that directory and an update-pending marker;
6. records, but never deletes, the private application-data `enmotion-output`
   directory.

Only then may the signed platform installer replace the application. On the
first successful launch, the new Rust shell requires an authenticated sidecar
readiness proof for the expected version. The pending transaction is committed
to `.desktop-update-last-good.json` only after the frontend hydrates, completes
its login/session probe and critical API bootstrap, then invokes
`desktop_confirm_ui_ready`. Rust forwards the webview's HttpOnly employee
cookie only to the nonce-protected sidecar, which asks the control plane to
confirm the employee and session are currently active before it commits.
Sidecar process readiness or an unauthenticated
frontend signal alone never marks an update healthy. If launch fails before
that point, the pending transaction and backup remain available for recovery.

Schema migrations must follow the same protocol. A future migration must be
backward-readable or write to a versioned staging file and atomically rename it
only after validation. In-place destructive schema changes are prohibited.

## Release acceptance criteria

A release is publishable only when all of these pass on clean machines:

- macOS Apple silicon, macOS Intel, and Windows x64 bundles build from the same
  version and commit; no other desktop target is produced.
- The sidecar binds only to `127.0.0.1`, rejects an incorrect Host, nonce, origin,
  and cookie, and Tauri refuses navigation to a different origin or port.
- A normal first launch, login, image generation, video generation, cancellation,
  export, quit, and relaunch complete without data loss.
- Cold and warm sidecar startup times are measured on every supported clean
  machine; the native loading window stays responsive, and startup fails with
  a clear diagnostic rather than waiting longer than 120 seconds.
- The default output is exactly the operating system's private application-data
  `enmotion-output` directory, including when the path contains spaces or
  non-ASCII characters, and requires no Documents or Full Disk Access grant.
- An invalid updater signature, changed archive, HTTP endpoint, missing signing
  secret, expired/reused capability, inactive employee, or unavailable asset
  fails closed.
- Checking and downloading do not freeze navigation or active work. Installing
  is refused while work is active, and becomes available after that work
  reaches a terminal state.
- Updating from the previous supported version preserves account configuration,
  projects, series, asset metadata, and generated media.
- Killing power/process before backup publication leaves the old metadata
  authoritative. Killing it after publication but before install leaves a
  recoverable pending transaction. Killing it during install leaves either the
  previous signed application or the new signed application launchable.
- The new version commits the transaction only after authenticated readiness.
- Rollback installs the previous signed release without downgrading or deleting
  user data, restores the saved metadata only when its schema requires it, and
  starts successfully against the same output directory.
- The macOS application and DMG pass `codesign`, Gatekeeper, notarization, and
  stapling checks. The Windows sidecar, application, and installer pass
  Authenticode verification.
- Every published file is below GitHub's 2 GiB asset limit and matches
  `SHA256SUMS`; each target has a CycloneDX SBOM. GitHub build-provenance
  attestations are required for every public release.
- The public release page exposes the two DMGs and signed Windows setup EXE for
  first installation. The updater inventory uses only versioned
  `/releases/download/desktop-vX.Y.Z/` URLs, never `/releases/latest/`.

The release job must remain a draft until all three native target jobs and the
aggregate verification job pass. Automatic binary rollback is not claimed:
until clean-machine interruption tests prove the platform installer behavior,
operators must retain the previous signed installer and use the tested rollback
procedure above.
