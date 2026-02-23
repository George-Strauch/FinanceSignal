#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

docker build -t financesignal:latest -f "$SCRIPT_DIR/Dockerfile" "$PROJECT_DIR"
echo "Build complete: financesignal:latest"
