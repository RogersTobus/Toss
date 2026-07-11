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
sudo journalctl -u toss.service -f
sudo journalctl -u toss-autodeploy.service -f
```

The `.env` file stays only on the server and is not committed to Git.
