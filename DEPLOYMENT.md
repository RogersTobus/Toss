# Lightsail automatic deployment

Run this once inside the Lightsail SSH terminal:

```bash
cd ~/Toss
git pull
chmod +x scripts/install_lightsail.sh
./scripts/install_lightsail.sh
```

After that, the server keeps running in the background and checks GitHub every
minute. When `origin/main` changes, it pulls the new code and restarts the app.

Useful commands:

```bash
sudo systemctl status toss.service
sudo systemctl status toss-autodeploy.timer
sudo systemctl status toss-research.timer
sudo journalctl -u toss.service -f
sudo journalctl -u toss-autodeploy.service -f
sudo journalctl -u toss-research.service -f
```

The `.env` file stays only on the server and is not committed to Git.

The research timer starts a separate oneshot process every ten minutes. It has
a 384 MB memory ceiling and a 45% CPU quota, pauses during KR/US regular
trading, and runs intraday replay and daily/weekly/monthly research
sequentially. The dashboard reads `research_worker_state.json` for its
heartbeat and progress. Bulky replay and chart results live in the separate
`research_learning_state.json`, so the worker never loads the live trading
brain. Both state files are intentionally not committed.
