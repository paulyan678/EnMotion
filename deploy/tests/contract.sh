#!/bin/sh
set -eu

TEST_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DEPLOY_DIR=$(CDPATH= cd -- "$TEST_DIR/.." && pwd)

for script in "$DEPLOY_DIR"/bin/*; do
    case "$script" in
        *.sh|*/backup|*/bootstrap|*/doctor|*/export-transfer|*/restore|*/start|*/status|*/stop|*/container-entrypoint)
            sh -n "$script"
            ;;
    esac
done

temp_dir=$(mktemp -d "${TMPDIR:-/tmp}/enmotion-deploy-contract.XXXXXX")
cleanup() {
    rm -rf -- "$temp_dir"
}
trap cleanup EXIT INT TERM

env_file=$temp_dir/.env.server
cp "$DEPLOY_DIR/.env.server.example" "$env_file"
chmod 0600 "$env_file"

awk '
    /^POSTGRES_PASSWORD=/ { print "POSTGRES_PASSWORD=ContractOnly123"; next }
    /^ENMOTION_SESSION_SECRET=/ { print "ENMOTION_SESSION_SECRET=0123456789abcdef0123456789abcdef0123456789abcdef"; next }
    { print }
' "$env_file" >"$temp_dir/env.next"
mv "$temp_dir/env.next" "$env_file"
chmod 0600 "$env_file"

ENMOTION_ENV_FILE=$env_file ENMOTION_DEPLOY_MODE=mac "$DEPLOY_DIR/bin/doctor" --static

grep -q 'database:' "$DEPLOY_DIR/compose.yaml"
grep -q 'redis:' "$DEPLOY_DIR/compose.yaml"
grep -q 'worker:' "$DEPLOY_DIR/compose.yaml"
grep -q 'edge:' "$DEPLOY_DIR/compose.yaml"
grep -q 'internal: true' "$DEPLOY_DIR/compose.yaml"

printf 'portable deployment shell contracts passed\n'
