#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="financesignal"

if docker container inspect "$CONTAINER_NAME" &>/dev/null; then
    echo "Stopping $CONTAINER_NAME..."
    docker stop "$CONTAINER_NAME" && docker rm "$CONTAINER_NAME"
    echo "Stopped."
else
    echo "Container '$CONTAINER_NAME' is not running."
fi
