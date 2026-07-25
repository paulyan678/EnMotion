# Asset Library performance harness

These scripts create isolated, synthetic Asset Library workspaces and measure
the authenticated browser path without calling an AI provider.

## Safety

- Fixture usernames must start with `enmotion-perf-`.
- The seeder refuses to replace or remove a non-fixture workspace.
- Passwords are read from standard input, never command-line arguments.
- Browser reports contain aggregate URL classes, not cookies, signed query
  strings, credentials, or private filenames.
- Remove fixture accounts after the run with `--cleanup`.

## Seed an isolated developer server

This harness targets the retained browser-development server modules, not the
production control plane or an employee's desktop workspace. Use an isolated
SQLite database and output root:

```sh
export ENMOTION_SERVER_MODE=true
export DATABASE_URL=sqlite:////tmp/enmotion-performance.db
export ENMOTION_SESSION_SECRET=local-performance-session-secret-32-chars
export ENMOTION_WORKSPACE_ROOT=/tmp/enmotion-performance-workspaces

python -m src.apps.server.cli migrate
printf '%s\n' "$ENMOTION_FIXTURE_PASSWORD" |
  python scripts/performance/seed_asset_library.py \
    --username enmotion-perf-primary \
    --assets 2000 \
    --unique-images 50 \
    --include-broken \
    --password-stdin
```

Generate or resume all referenced image derivatives without occupying the
interactive worker queue:

```sh
python -m src.apps.server.derivatives_cli --limit-per-workspace 10000
```

Start the browser development harness with `npm run dev` before collecting
measurements.

## Run browser measurements

From `frontend/`, pass credentials only through the environment:

```sh
ENMOTION_PERF_BASE_URL=http://127.0.0.1:3008 \
ENMOTION_PERF_USERNAME=enmotion-perf-primary \
ENMOTION_PERF_PASSWORD="$ENMOTION_FIXTURE_PASSWORD" \
ENMOTION_PERF_BROWSER=chromium \
ENMOTION_PERF_PROFILE=fast-4g \
ENMOTION_PERF_CACHE_MODE=both \
ENMOTION_PERF_CYCLES=10 \
ENMOTION_PERF_SESSIONS=5 \
ENMOTION_PERF_OUTPUT=../artifacts/performance/asset-library.json \
npm run perf:asset-library
```

Supported browsers are `chromium`, `firefox`, and `webkit`. Profiles are
`desktop`, `fast-4g`, `slow-4g`, and `mobile-fast-4g`. Supplying both
`ENMOTION_PERF_SECONDARY_USERNAME` and `ENMOTION_PERF_SECONDARY_PASSWORD`
alternates sessions between two isolated workspaces.

## Cleanup

```sh
python scripts/performance/seed_asset_library.py \
  --username enmotion-perf-primary --cleanup
```
