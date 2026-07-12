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

python3 - "$LOCAL_COMMIT" "$REMOTE_COMMIT" <<'PY'
import json
import sys
import urllib.request
from pathlib import Path

old_commit, new_commit = sys.argv[1:3]
env_path = Path(".env")
env = {}
if env_path.exists():
    for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")

enabled = env.get("SLACK_LOG_ENABLED", "").lower() in ("1", "true", "yes", "on")
webhook = env.get("SLACK_LOG_WEBHOOK_URL", "")
if enabled and webhook:
    payload = {
        "text": f":white_check_mark: *Orbit 자동배포 완료*\\n{old_commit[:7]} → {new_commit[:7]}"
    }
    request = urllib.request.Request(
        webhook,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=8).read()
    except Exception:
        pass
PY
