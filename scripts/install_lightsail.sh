#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/home/ubuntu/Toss}"

if [ ! -d "$APP_DIR/.git" ]; then
  echo "Toss Git repository was not found at $APP_DIR"
  echo "Run this first: git clone https://github.com/RogersTobus/Toss.git $APP_DIR"
  exit 1
fi

cd "$APP_DIR"

git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
sudo git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true

if [ ! -f "$APP_DIR/.env" ] && [ -f "$APP_DIR/.env.example" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo ".env was created. Fill it before relying on the service:"
  echo "nano $APP_DIR/.env"
fi

chmod +x "$APP_DIR/scripts/deploy.sh"

sudo cp "$APP_DIR/scripts/toss.service" /etc/systemd/system/toss.service
sudo cp "$APP_DIR/scripts/toss-autodeploy.service" /etc/systemd/system/toss-autodeploy.service
sudo cp "$APP_DIR/scripts/toss-autodeploy.timer" /etc/systemd/system/toss-autodeploy.timer

sudo systemctl daemon-reload
sudo systemctl reset-failed toss.service toss-autodeploy.service || true
sudo systemctl enable --now toss.service
sudo systemctl enable --now toss-autodeploy.timer
sudo systemctl restart toss.service
sudo systemctl restart toss-autodeploy.timer

echo "Toss service is installed."
echo "App:     sudo systemctl status toss.service"
echo "Updater: sudo systemctl status toss-autodeploy.timer"
echo "Logs:    sudo journalctl -u toss.service -f"
