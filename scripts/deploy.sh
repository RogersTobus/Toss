#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/home/ubuntu/Toss}"
BRANCH="${BRANCH:-main}"
SERVICE_NAME="${SERVICE_NAME:-toss.service}"

cd "$APP_DIR"

git fetch origin "$BRANCH"

LOCAL_COMMIT="$(git rev-parse HEAD)"
REMOTE_COMMIT="$(git rev-parse "origin/$BRANCH")"

if [ "$LOCAL_COMMIT" = "$REMOTE_COMMIT" ]; then
  echo "Toss is already up to date: $LOCAL_COMMIT"
  exit 0
fi

git pull --ff-only origin "$BRANCH"

if [ "$(id -u)" -eq 0 ]; then
  systemctl restart "$SERVICE_NAME"
else
  sudo systemctl restart "$SERVICE_NAME"
fi

echo "Toss deployed: $REMOTE_COMMIT"
