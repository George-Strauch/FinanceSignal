#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONTAINER_NAME="financesignal"

# Stop existing container if running
if docker container inspect "$CONTAINER_NAME" &>/dev/null; then
    echo "Stopping existing container..."
    docker stop "$CONTAINER_NAME" && docker rm "$CONTAINER_NAME"
fi

echo "Starting $CONTAINER_NAME..."
docker run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    -p 8000:8000 \
    -v "$PROJECT_DIR:/app/data" \
    financesignal:latest

echo "Container started: $(docker ps --filter name=$CONTAINER_NAME --format '{{.ID}}')"
echo "http://localhost:8000"
