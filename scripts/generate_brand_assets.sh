#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAURI_ICON_VERSION="${TAURI_CLI_VERSION:-2.11.4}"
MASTER_ICON="$REPOSITORY_ROOT/brand/enmotion-app-icon.svg"
BRAND_ICON_TMP="$(mktemp -d "${TMPDIR:-/tmp}/enmotion-brand-icons.XXXXXX")"

cleanup() {
  rm -rf "$BRAND_ICON_TMP"
}
trap cleanup EXIT

for source in \
  "$MASTER_ICON" \
  "$REPOSITORY_ROOT/brand/enmotion-mark.svg" \
  "$REPOSITORY_ROOT/brand/enmotion-mark-on-dark.svg" \
  "$REPOSITORY_ROOT/brand/enmotion-lockup.svg" \
  "$REPOSITORY_ROOT/brand/enmotion-lockup-on-dark.svg"; do
  if [[ ! -s "$source" ]]; then
    echo "Missing canonical brand asset: $source" >&2
    exit 1
  fi
done

npx --yes "@tauri-apps/cli@$TAURI_ICON_VERSION" icon \
  "$MASTER_ICON" \
  --output "$BRAND_ICON_TMP"

install -m 0644 "$BRAND_ICON_TMP/icon.icns" "$REPOSITORY_ROOT/icon.icns"
install -m 0644 "$BRAND_ICON_TMP/icon.ico" "$REPOSITORY_ROOT/icon.ico"
install -m 0644 \
  "$BRAND_ICON_TMP/icon.png" \
  "$REPOSITORY_ROOT/desktop/src-tauri/icons/icon.png"
install -m 0644 \
  "$BRAND_ICON_TMP/icon.ico" \
  "$REPOSITORY_ROOT/frontend/src/app/favicon.ico"
install -m 0644 \
  "$BRAND_ICON_TMP/icon.ico" \
  "$REPOSITORY_ROOT/control_plane/app/static/admin/favicon.ico"

install -m 0644 \
  "$REPOSITORY_ROOT/brand/enmotion-mark-on-dark.svg" \
  "$REPOSITORY_ROOT/control_plane/app/static/admin/logo.svg"
install -m 0644 \
  "$REPOSITORY_ROOT/brand/enmotion-lockup.svg" \
  "$REPOSITORY_ROOT/frontend/public/enmotion-lockup.svg"
install -m 0644 \
  "$REPOSITORY_ROOT/brand/enmotion-lockup-on-dark.svg" \
  "$REPOSITORY_ROOT/frontend/public/enmotion-lockup-on-dark.svg"

echo "Generated official EnMotion web, control-plane, macOS, and Windows brand assets."
