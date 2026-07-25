#!/bin/sh

set -eu

echo "========================================"
echo "Starting Backend (FastAPI)..."
echo "========================================"

# 确保在项目根目录
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

if ! command -v node >/dev/null 2>&1; then
    echo "Error: Node.js 20 is required to launch the configured backend." >&2
    exit 1
fi

# The shared launcher safely reads .env, validates API_PORT, preserves the
# caller's proxy exclusions, and starts the repository virtual environment.
exec node "$SCRIPT_DIR/scripts/start-backend.js"
