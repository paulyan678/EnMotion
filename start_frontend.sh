#!/bin/sh

set -eu

echo "========================================"
echo "Starting Frontend (Next.js)..."
echo "macOS note: npm run dev now enables a stable watcher path automatically."
echo "Default frontend dev port: 3008"
echo "========================================"

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR/frontend"

# 检查 node_modules 是否存在
if [ ! -d "node_modules" ]; then
    echo "⚠️  node_modules not found. Installing dependencies..."
    npm ci
    echo "✅ Dependencies installed."
fi

exec npm run dev
