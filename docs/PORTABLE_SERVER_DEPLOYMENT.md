# Portable server deployment

## Purpose and safety boundary

The portable deployment turns `enmotion-web` into a single-host, multi-user web
service. It is separate from the original `enmotion` desktop repository. It uses
the same application bundle on a test Mac and a production Linux server, while
keeping runtime state and secrets outside the application ZIP.

The deployment provides:

- an HTTPS-capable Caddy edge with the only host-published ports;
- a static Next.js frontend;
- the authenticated FastAPI service;
- PostgreSQL for users, workspaces, sessions, quotas, and durable jobs;
- Redis and a dedicated generation worker;
- private Docker volumes for database data, media, and application data;
- a shared persistent model cache for Demucs/Torch weights;
- non-root runtime users, dropped capabilities, health checks, and isolated
  container networks;
- migrations, administrator bootstrap, checksummed backups, destructive-restore
  confirmation, and portable transfer archives.

This is a strong single-server foundation, not a high-availability cluster.
PostgreSQL, Redis, API, and worker all live on one host. Add external database,
object storage, and an orchestrator before requiring multi-host failover.

## Architecture

```text
browser
   |
   | HTTP on Mac / HTTPS in production
   v
Caddy edge  ----> static frontend
   |
   +-----------> FastAPI
                    |  \
                    |   +----> private media + app-data volumes
                    |
                    +--------> PostgreSQL
                    +--------> Redis <---- generation worker
```

The `private` Compose network is marked `internal`. PostgreSQL and Redis have no
host ports. The API and frontend use `expose`, not `ports`. Only the edge
override publishes a port to the Mac or server.

## Files and contracts

| Path | Role |
| --- | --- |
| `deploy/compose.yaml` | OS-independent services, networks, and volumes |
| `deploy/compose.mac.yaml` | loopback-only Mac HTTP override |
| `deploy/compose.production.yaml` | public HTTP/HTTPS and secure-cookie override |
| `deploy/.env.server.example` | committed, secret-free configuration template |
| `deploy/.env.server` | generated private configuration; ignored by Git/Docker |
| `deploy/VERSION` | application bundle version |
| `deploy/SCHEMA_VERSION` | portable state-format compatibility version |
| `deploy/manifests/deployment.json` | API, worker, migration, and platform contract |
| `deploy/bin/*` | lifecycle, validation, backup, restore, and export tools |

Mutating lifecycle commands (`bootstrap`, `start`, `stop`, `backup`, and
`restore`) share one host-side lock. The lock records both a PID and that
process's start identity, so an interrupted command is recoverable without
mistaking a later PID reuse for the original owner. Do not delete a live lock;
the scripts remove a demonstrably stale one themselves.

The backend image expects these application commands:

```text
API:       python -m uvicorn src.apps.comic_gen.api:app
Migrate:   python -m src.apps.server.cli migrate
Bootstrap: python -m src.apps.server.cli bootstrap-admin
Worker:    $ENMOTION_WORKER_COMMAND
```

The default worker command is `python -m src.apps.server.worker`. PostgreSQL is
provided as `DATABASE_URL`; Redis is provided as `REDIS_URL` and
`ENMOTION_QUEUE_REDIS_URL`. Media is mounted at `/app/output`, and credential-
bearing application data is mounted at `/data`. Tenant pipeline roots live at
`/app/output/workspaces`, inside the same private media volume captured by every
state backup. Existing EnMotion project, series, and library documents remain
JSON, but each account gets a different root at
`/app/output/workspaces/<workspace-id>/output`. The API and worker bind an
authenticated tenant context and take a per-workspace cross-process lock before
touching those documents. A user cannot select or address another workspace.

Provider credentials and runtime model selections are operator-wide settings,
not per-user settings. Only an administrator can read or change them. Ordinary
users create projects and media only inside their personal workspace.

The browser/API CSRF protocol uses the fixed `enmotion_csrf` cookie and
`X-CSRF-Token` request header. These public protocol names are intentionally not
configurable; startup fails if legacy environment overrides try to rename them.

## Mac prerequisites

Install Docker Desktop for Mac with the Compose v2 plugin. The scripts require
Docker Compose 2.24 or newer. Allocate enough Docker Desktop resources for AI
and video processing; 8 CPU cores, 16 GB memory, and at least 50 GB free disk is
a practical development starting point. Large generated videos and Demucs model
weights can require substantially more disk.

Check the runtime:

```sh
docker version
docker compose version
uname -m
```

An Apple Silicon Mac prints `arm64`. The Compose stack intentionally has no
hard-coded `platform`; it builds Linux ARM64 images natively on that Mac.

## First Mac deployment

From the `enmotion-web` repository root:

```sh
deploy/bin/bootstrap --mode mac
```

The command performs the complete safe initialization:

1. copies `.env.server.example` to the ignored `deploy/.env.server` file;
2. sets file mode `0600`;
3. generates URL-safe PostgreSQL and session secrets;
4. validates configuration and Compose syntax;
5. builds images for the Mac's native architecture;
6. starts PostgreSQL and Redis and waits for health;
7. applies all Alembic migrations;
8. prompts for the initial administrator without echoing the password;
9. starts and health-checks API, worker, frontend, and edge.

Open [http://127.0.0.1:8080](http://127.0.0.1:8080). Then run:

```sh
deploy/bin/status --mode mac
deploy/bin/doctor --mode mac
```

The bootstrap administrator receives a 20 GiB personal workspace. Accounts
created from the administrator UI also default to 20 GiB, and the administrator
can choose another quota at creation time. Generation admission additionally
limits active and rolling-daily jobs so one account cannot monopolize the
worker or unexpectedly fill the server.

Login failures are limited separately by client IP and username. The default is
5 failures per IP and a higher 25-failure account-wide threshold per five
minutes. Successful authentication removes its IP reservation and resets the
proven account bucket, so normal logins do not lock out other users.
Expired and revoked session rows are pruned on successful login, and each
account keeps at most 20 active sessions by default. Configure
`ENMOTION_MAX_ACTIVE_SESSIONS_PER_USER` when a different device limit is needed.

For unattended bootstrap, pass credentials through the process environment.
They are forwarded to one temporary container and are not written to
`.env.server`:

```sh
ENMOTION_BOOTSTRAP_ADMIN_USERNAME=admin \
ENMOTION_BOOTSTRAP_ADMIN_PASSWORD='use-a-long-unique-password' \
deploy/bin/bootstrap --mode mac
```

Clear those shell variables afterward if they were exported. Interactive
bootstrap is preferred because it avoids shell history.

### LAN-only testing

The default bind address is `127.0.0.1`, so no other machine can connect. To
test from a trusted local network, set the following in `deploy/.env.server`:

```dotenv
ENMOTION_BIND_HOST=0.0.0.0
ENMOTION_ALLOWED_ORIGINS=http://192.168.1.20:8080
```

Replace the IP with the Mac's LAN address, restart, and allow only the private
LAN in the macOS firewall. Do not forward this HTTP port from a router or expose
Mac mode directly to the internet.

## Routine operation

```sh
# Apply migrations and start the existing images
deploy/bin/start --mode mac

# Rebuild, migrate, and start after source changes
deploy/bin/start --mode mac --build

# Inspect containers and health
deploy/bin/status --mode mac
deploy/bin/doctor --mode mac

# Stop containers but preserve every named volume
deploy/bin/stop --mode mac
```

Never use `docker compose down --volumes` unless the exact named volumes have
been backed up and permanent deletion is intentional.

Startup checks the live Alembic revision before migration and verifies that the
database reaches the exact bundled head afterward. This prevents an older app
bundle from starting against a database created by a newer or unrelated
migration history.

## Backups

Backups briefly stop the edge, API, and worker to prevent writes while capturing
one consistent PostgreSQL/media/app-data state. The script creates a logical
PostgreSQL custom-format dump rather than copying a live database volume.

Create a local state backup:

```sh
deploy/bin/backup --mode mac
```

The resulting `backups/enmotion-state-<UTC>.tar.gz` contains:

```text
database.dump
media.tar.gz
app-data.tar.gz
manifest.json
SHA256SUMS
```

Backups are assembled under unique temporary names and published with a
no-clobber atomic rename only after the complete archive (and, when requested,
its encryption) succeeds. A failed or interrupted backup removes its partial
files, and a same-name archive is never overwritten.

It never contains `deploy/.env.server`. However, `app-data.tar.gz` can contain
provider keys saved through the web settings UI, and the database contains user
records and session data. Treat every state archive as sensitive.

For transfer or off-server storage, use encryption. Put a long random
passphrase in an owner-readable file outside the repository, then run:

```sh
deploy/bin/backup --mode mac \
  --passphrase-file /secure/path/enmotion-backup.pass
```

This creates an AES-256-CBC/PBKDF2 `.tar.gz.enc` archive. Store the passphrase
separately from the archive. Schedule production backups from the host, copy
them off-server, enforce retention, and alert on failures. A backup is not
proven until a restore drill succeeds. Production mode refuses to create a
plaintext backup and requires an owner-only (`0600` or `0400`) passphrase file.

Redis is queue transport, not authoritative state, and is intentionally not in
the backup. Durable job records live in PostgreSQL; the worker reconciles them
after restart without blindly resubmitting ambiguous provider requests.
The `model_cache` volume is a rebuildable performance cache and is also omitted;
Demucs/Torch will download missing weights again after a clean destination move.

### Alibaba OSS is external state

When `OSS_ENABLE=true`, a EnMotion state archive is **not self-contained**. The
archive records that OSS was enabled, but it does not copy objects from the
bucket and never includes `deploy/.env.server`. Backup checks both the host
environment and the administrator-managed setting persisted in
`app-data/config.json`, treating either enabled value conservatively as external
state. Restore onto a host with access to the same bucket and object prefix, or
separately version and back up the OSS bucket and restore those objects first.
Transfer the matching endpoint, bucket, prefix, and credentials through a
secrets channel. A database/media archive without the corresponding OSS objects
can contain references whose media is no longer available.

## Remote media safety and limits

Server mode does not fetch arbitrary URLs supplied by a user or returned by a
provider. Every remote image/video host must match
`ENMOTION_REMOTE_MEDIA_HOSTS`, resolve only to public addresses, and stay within
the configured byte limit. The example permits the default `moyu.cn` gateway
family and Alibaba OSS subdomains:

```dotenv
ENMOTION_REMOTE_MEDIA_HOSTS=www.moyu.cn,*.moyu.cn,*.aliyuncs.com
ENMOTION_REMOTE_IMAGE_MAX_BYTES=26214400
ENMOTION_REMOTE_MEDIA_MAX_BYTES=536870912
```

If your relay returns a URL on a separate CDN, add that exact host (or the
narrowest safe wildcard) and restart API and worker. Do not use broad patterns
such as `*.com`. Local references, including absolute paths submitted through
editable asset fields, are accepted only when their resolved target stays
inside the authenticated workspace. Provider downloads and inline image data
are size-bounded before they are written.

Job controls are configured in `deploy/.env.server`. Defaults allow 10 active
jobs and 100 jobs per rolling 24 hours per workspace. Reservations are 512 MiB
for images, at least 1 GiB for single-video and dub work, and 4 GiB for batch,
full-storyboard/video, merge, and export work. These long operations run in the
durable worker; the web client polls their PostgreSQL-backed job record and then
reloads the project. Actual files are checked against the workspace quota after
they are saved.

Serialized job metadata is capped at 256 KiB per job before PostgreSQL insert.
Terminal history is retained for 30 days and keeps the newest 500 records per
workspace by default; queued/running jobs and every job finished in the last 24
hours are never compacted even if that temporarily exceeds 500. Provider work
that completes while PostgreSQL is
temporarily unavailable records a private intent in the backed-up app-data
volume, allowing the worker to finalize the row later without repeating the AI
request. Tune the `ENMOTION_MAX_JOB_PAYLOAD_BYTES`,
`ENMOTION_JOB_HISTORY_RETENTION_DAYS`, and
`ENMOTION_MAX_TERMINAL_JOBS_PER_WORKSPACE` settings conservatively.

The edge rejects request bodies larger than 16 MiB by default through
`ENMOTION_MAX_REQUEST_BODY_BYTES`. This is an outer safety ceiling; individual
application uploads remain more restrictive (5 MiB for text, 8 or 10 MiB for
images depending on the endpoint, and 10 MiB for audio). Keep the edge value at
or above those per-file limits.

## Restore

Restore replaces the destination database, media volume, and application-data
volume. It refuses to run without `--yes`, validates archive paths and every
SHA-256 checksum, rejects a newer application/state schema or an Alembic
revision outside the bundled history, and validates the PostgreSQL dump before
the destructive boundary. It then stops write-producing services and captures
an automatic `backups/enmotion-pre-restore-<UTC>.tar.gz[.enc]` rollback archive of
the exact paused destination state before replacing anything.

If replacement fails after it begins, the script deliberately leaves the edge,
API, and worker stopped. It prints the rollback archive path; restore that
archive and run `doctor` before starting public services. It never automatically
resumes a possibly mixed database/media state. After a successful restore, it
checks that the dump's actual Alembic revision matches its manifest, migrates
forward, verifies the bundled head, resumes only the services that were running,
and retains the rollback archive for operator review.

```sh
deploy/bin/restore --mode mac --yes \
  backups/enmotion-state-20260719T120000Z.tar.gz
```

For an encrypted backup:

```sh
deploy/bin/restore --mode mac --yes \
  --passphrase-file /secure/path/enmotion-backup.pass \
  backups/enmotion-state-20260719T120000Z.tar.gz.enc
```

Production restore accepts encrypted `.enc` archives only. The passphrase file
must be a non-symlink owner-only file (`0600` or `0400`). Mac mode still permits
plaintext local restore for convenient testing, while encrypted restore follows
the same rule on both platforms.

If a previous failed restore left the destination database too damaged for the
normal automatic rollback snapshot or live-schema check, use the explicit
recovery path:

```sh
deploy/bin/restore --mode mac --recovery --yes \
  --passphrase-file /secure/path/enmotion-backup.pass \
  backups/enmotion-state-20260719T120000Z.tar.gz.enc
```

Recovery mode still validates the archive, checksums, PostgreSQL dump, bundle
version, and bundled Alembic compatibility before replacement. It deliberately
skips the destination rollback archive and live-schema validation, so use it
only when normal restore cannot inspect the damaged destination. If recovery
fails after replacement begins, services remain stopped until the operator
repairs the destination or retries a verified archive.

When a manifest says its projects reference external object storage that is not
inside the archive, restore fails closed. First verify or restore the matching
bucket data and configuration; then acknowledge that separate state explicitly
with `--allow-external-state`.

Run `deploy/bin/doctor --mode mac` after every restore and sign in to verify a
project, uploaded asset, generated video, and job history.

## Create portable transfer archives

Create a reusable application bundle:

```sh
deploy/bin/export-transfer
```

The application ZIP is generated from committed source when Git metadata is
available. It excludes `.env.server`, runtime outputs, backups, transfers,
private keys, and local build caches, then scans the ZIP and refuses export if a
secret-bearing filename is present. Commit intentional application changes
before exporting.

Every ZIP embeds `deploy/SOURCE_MANIFEST.sha256`, `deploy/SOURCE_IDENTITY`, and
`deploy/TRANSFER_PROVENANCE.txt`. The manifest gives the extracted no-Git tree a
stable content/inventory identity, so later builds and exports reject source
edits that do not match the transferred bundle. Running-image checks compare
that identity, revision, application version, immutable image ID, and source
state for all six services; a stale, dirty, or partially running stack blocks
export. These records provide traceability, not a cryptographic code signature,
so verify the separately transferred `.SHA256` sidecar through a trusted
channel.

Export builds each artifact under a unique private staging directory and uses
same-filesystem no-clobber publication. Application ZIPs and complete-transfer
ZIPs have random-suffixed names and SHA-256 sidecars; an existing state archive
or checksum is reused only when it is already the selected source and matches,
and is otherwise never overwritten.

This source bundle is architecture-independent, but intentionally does not
embed Docker images, language-package caches, or base images. The destination
needs internet access to rebuild the pinned application dependencies. That is
what lets an Apple Silicon Mac bundle move to an AMD64 Linux server without
shipping incompatible images. A truly offline handoff requires a separately
prepared image registry/archive for the destination architecture and is outside
the source ZIP contract.

Keep application and state separate (recommended):

```sh
deploy/bin/export-transfer \
  --with-state backups/enmotion-state-20260719T120000Z.tar.gz.enc
```

For a one-file handoff, explicitly request a wrapper ZIP:

```sh
deploy/bin/export-transfer \
  --with-state backups/enmotion-state-20260719T120000Z.tar.gz.enc \
  --complete
```

The complete ZIP still contains private state even though its nested
application ZIP is secret-free. `--complete` accepts only an OpenSSL salted
encrypted `.enc` state archive and rejects plaintext or a renamed plaintext
file. It also publishes its own `.SHA256` sidecar. Transfer over an authenticated
channel, and delete temporary copies after the destination checksum is verified.

## Move from Apple Silicon to a Linux server

The recommended workflow transfers source and rebuilds on the destination. It
does not copy Mac-built ARM64 images to an AMD64 server.

1. Create an application ZIP and encrypted state archive on the Mac.
2. Copy both, their `.SHA256` files, and the passphrase through separate secure
   channels when possible.
3. Install Docker Engine and Compose v2 on the Linux server.
4. Verify checksums, unzip the application, and enter its directory.
5. Copy `deploy/.env.server.example` to `deploy/.env.server`, set the production
   domain, HTTPS origin, and `ENMOTION_BIND_HOST=0.0.0.0`, then run
   `chmod 600 deploy/.env.server`. Leave both generated-secret placeholders as
   `__GENERATE__`; bootstrap replaces them locally.
6. Run `deploy/bin/bootstrap --mode production --skip-admin` once to generate
   destination secrets and create the images and volumes.
7. Restore the transferred state with `deploy/bin/restore --mode production`.
   Add `--allow-external-state` only after separately verifying any omitted OSS
   bucket state described above.
8. Run `deploy/bin/doctor --mode production` and complete the isolation checks.

Native destination builds work for both `linux/amd64` and `linux/arm64`. A later
release can publish a multi-platform manifest to a private registry to avoid
destination builds, but the source ZIP remains architecture-independent.

## Production configuration

Before production bootstrap, create `deploy/.env.server` from the example and
set at least:

```dotenv
ENMOTION_BIND_HOST=0.0.0.0
ENMOTION_DOMAIN=studio.example.com
ENMOTION_ALLOWED_ORIGINS=https://studio.example.com
ENMOTION_COOKIE_SECURE=true
```

Production commands reject a missing or loopback `ENMOTION_BIND_HOST`; the Mac
template's active `127.0.0.1` value must be changed explicitly. This prevents a
successful-looking public deployment that is reachable only from the server.

Keep `deploy/.env.server` as a regular, non-symlink file with mode `0600` (or
read-only `0400`). Production lifecycle commands fail closed on broader
permissions; Mac mode reports them through `doctor` without blocking local
experiments.

Use a DNS `A`/`AAAA` record pointing at the server and allow inbound TCP 80,
TCP 443, and UDP 443. Caddy obtains and renews certificates automatically.
PostgreSQL, Redis, API port 17177, and frontend port 8080 must remain blocked
from the public network. Caddy adds a one-year HSTS header only on TLS requests;
loopback Mac HTTP responses do not include HSTS.

`/healthz` is the cheap public edge liveness endpoint. The API's `/ready` probe
queries PostgreSQL and Redis and is intentionally blocked at Caddy; Docker calls
it only over the private container network. Worker health checks process
liveness rather than Celery remote control, so a legitimate long solo job does
not mark its own container unhealthy.

Generate fresh PostgreSQL/session secrets with `bootstrap`; do not reuse the
example placeholders. When migrating state, a new session secret safely logs
every user out. Retaining the old secret preserves existing session signatures,
but it must be transferred separately because backups deliberately omit
`.env.server`.

Production bootstrap:

```sh
deploy/bin/bootstrap --mode production
deploy/bin/doctor --mode production
```

Also configure host-level disk monitoring, off-server encrypted backups, log
collection, NTP, security updates, and alerts for container health, disk
pressure, queue backlog, generation errors, and backup failures.
Compose rotates each service's Docker `json-file` log at 10 MiB with five files
by default; tune `ENMOTION_LOG_MAX_SIZE` and `ENMOTION_LOG_MAX_FILES` together with
your host collector and retention policy.

## Validation and release gates

Run source-level deployment checks on any POSIX host:

```sh
deploy/tests/contract.sh
python -m pytest tests/test_portable_deployment.py -q
```

With Docker installed, validate rendered Compose configurations:

```sh
deploy/bin/doctor --mode mac --static
docker compose --env-file deploy/.env.server \
  -f deploy/compose.yaml -f deploy/compose.mac.yaml config --quiet
```

Before production, exercise this acceptance sequence on a clean Mac Docker
Desktop installation and again on a clean Linux VM:

1. bootstrap from only the application ZIP;
2. create two users and confirm each gets a distinct personal workspace;
3. attempt cross-user list/read/write/delete/file/job access using changed IDs;
4. upload media, create a project, and finish one image/video job;
5. restart API and worker mid-job and verify recovery does not duplicate an AI
   provider submission;
6. restart Docker and verify all metadata/media persist;
7. create an encrypted backup, restore it onto empty volumes, and verify content;
8. verify only edge ports are reachable from another host;
9. inspect containers to confirm non-root runtime users and healthy status;
10. cut network access to Redis/PostgreSQL from outside Compose and confirm it
    remains inaccessible.

## Troubleshooting

- **`docker: command not found`**: install/start Docker Desktop or Docker Engine.
- **Compose too old**: upgrade the Compose v2 plugin to 2.24 or newer.
- **edge unhealthy on production**: verify DNS, public ports 80/443, firewall,
  domain spelling, and Caddy logs with `docker compose logs edge` using the same
  compose files and env file.
- **worker unhealthy**: inspect `docker compose logs worker`; confirm
  `ENMOTION_WORKER_COMMAND` names the bundled worker module, the ready marker can
  be created, and Redis is healthy. Long active jobs do not require a Celery
  control-ping response.
- **API unhealthy**: inspect API and migration logs; verify the session secret,
  database URL-safe password, database migration, and available disk.
- **restore rejected**: do not bypass path, checksum, encryption, or schema
  checks. Obtain an intact archive or install a compatible/newer app bundle.
- **Demucs is slow on first use**: its Torch/Demucs download cache is persisted
  in the `model_cache` volume. The first download can still be slow; optionally
  enable preload only after measuring Mac/server memory and disk requirements.
