#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/home/ubuntu/Toss}"
BRANCH="${BRANCH:-main}"
SERVICE_NAME="${SERVICE_NAME:-toss.service}"
AUTODEPLOY_SERVICE="${AUTODEPLOY_SERVICE:-toss-autodeploy.service}"
AUTODEPLOY_TIMER="${AUTODEPLOY_TIMER:-toss-autodeploy.timer}"

log() {
  printf '[%s] %s\n' "$(date -Is)" "$*"
}

run_systemctl() {
  if [ "$(id -u)" -eq 0 ]; then
    systemctl "$@"
  else
    sudo systemctl "$@"
  fi
}

write_deploy_stamp() {
  local status="$1"
  local deployed="$2"
  local local_commit="${3:-}"
  local remote_commit="${4:-}"
  local message="${5:-}"
  mkdir -p "$APP_DIR/.deploy"
  python3 - "$APP_DIR/.deploy/last_sync.json" "$status" "$deployed" "$local_commit" "$remote_commit" "$message" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path, status, deployed, local_commit, remote_commit, message = sys.argv[1:7]
payload = {
    "checkedAt": datetime.now(timezone.utc).isoformat(),
    "status": status,
    "deployed": deployed.lower() == "true",
    "localCommit": local_commit,
    "remoteCommit": remote_commit,
    "message": message,
}
Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
PY
}

if [ "$(id -u)" -eq 0 ]; then
  git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
else
  git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
  sudo git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
fi

cd "$APP_DIR"

log "Checking GitHub updates in $APP_DIR on branch $BRANCH"
git fetch origin "$BRANCH"

LOCAL_COMMIT="$(git rev-parse HEAD)"
REMOTE_COMMIT="$(git rev-parse "origin/$BRANCH")"

log "Local commit:  $LOCAL_COMMIT"
log "Remote commit: $REMOTE_COMMIT"

if [ "$LOCAL_COMMIT" = "$REMOTE_COMMIT" ]; then
  write_deploy_stamp "checked" "false" "$LOCAL_COMMIT" "$REMOTE_COMMIT" "already up to date"
  log "Toss is already up to date."
  exit 0
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  write_deploy_stamp "failed" "false" "$LOCAL_COMMIT" "$REMOTE_COMMIT" "uncommitted changes"
  log "Local working tree has uncommitted changes. Auto deploy stopped to avoid overwriting local edits."
  git status --short
  exit 1
fi

if ! git merge-base --is-ancestor "$LOCAL_COMMIT" "$REMOTE_COMMIT"; then
  write_deploy_stamp "failed" "false" "$LOCAL_COMMIT" "$REMOTE_COMMIT" "not fast-forward"
  log "Local branch cannot fast-forward to origin/$BRANCH. Manual review is required."
  exit 1
fi

git pull --ff-only origin "$BRANCH"

if [ -f "$APP_DIR/scripts/toss.service" ] && [ -f "$APP_DIR/scripts/$AUTODEPLOY_SERVICE" ] && [ -f "$APP_DIR/scripts/$AUTODEPLOY_TIMER" ]; then
  log "Refreshing systemd unit files."
  if [ "$(id -u)" -eq 0 ]; then
    cp "$APP_DIR/scripts/toss.service" /etc/systemd/system/toss.service
    cp "$APP_DIR/scripts/$AUTODEPLOY_SERVICE" "/etc/systemd/system/$AUTODEPLOY_SERVICE"
    cp "$APP_DIR/scripts/$AUTODEPLOY_TIMER" "/etc/systemd/system/$AUTODEPLOY_TIMER"
  else
    sudo cp "$APP_DIR/scripts/toss.service" /etc/systemd/system/toss.service
    sudo cp "$APP_DIR/scripts/$AUTODEPLOY_SERVICE" "/etc/systemd/system/$AUTODEPLOY_SERVICE"
    sudo cp "$APP_DIR/scripts/$AUTODEPLOY_TIMER" "/etc/systemd/system/$AUTODEPLOY_TIMER"
  fi
  run_systemctl daemon-reload
  run_systemctl enable "$AUTODEPLOY_TIMER"
fi

run_systemctl restart "$SERVICE_NAME"
run_systemctl restart "$AUTODEPLOY_TIMER"

write_deploy_stamp "deployed" "true" "$REMOTE_COMMIT" "$REMOTE_COMMIT" "updated from GitHub"
log "Toss deployed: $REMOTE_COMMIT"

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
