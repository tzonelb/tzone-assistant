# deploy/

Files in this directory, and where each one goes:

| File | Destination |
| --- | --- |
| `tzone-api.service` | `/etc/systemd/system/tzone-api.service` |
| `nginx.conf` | `/etc/nginx/sites-available/tzone` |
| `tzone-proxy.conf` | `/etc/nginx/snippets/tzone-proxy.conf` |
| `tzone-security-headers.conf` | `/etc/nginx/snippets/tzone-security-headers.conf` |
| `backup.cron` | `/etc/cron.d/tzone-backup` |

Full explanations are in [`../docs/DEPLOYMENT.md`](../docs/DEPLOYMENT.md).
The ordered commands are below.

## 1. Server account and directories

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx git
sudo adduser --system --group --home /opt/tzone --disabled-login tzone
sudo mkdir -p /opt/tzone /etc/tzone /var/backups/tzone /var/www/certbot
sudo chown -R tzone:tzone /opt/tzone /var/backups/tzone
```

## 2. Code and Python environment

```bash
sudo -u tzone git clone <REPO_URL> /opt/tzone
sudo -u tzone python3 -m venv /opt/tzone/venv
sudo -u tzone /opt/tzone/venv/bin/pip install --upgrade pip
sudo -u tzone /opt/tzone/venv/bin/pip install -r /opt/tzone/requirements.txt
sudo -u tzone mkdir -p /opt/tzone/data /opt/tzone/logs
```

## 3. Environment file

```bash
sudo cp /opt/tzone/.env.example /etc/tzone/tzone.env
sudo chown root:tzone /etc/tzone/tzone.env
sudo chmod 640 /etc/tzone/tzone.env
sudo -u tzone /opt/tzone/venv/bin/python -m tools.manage_platform generate-master-key
sudo nano /etc/tzone/tzone.env    # paste TZONE_MASTER_KEY, set META_APP_SECRET, DATA_DIR, LOG_DIR, CORS_ORIGINS, APP_ENV=production
```

Store the master key in an offline location as well. Without it every company
database on this server, and every backup of it, is unreadable forever.

## 4. First company

```bash
cd /opt/tzone
sudo -u tzone bash -c 'set -a; . /etc/tzone/tzone.env; set +a; \
  /opt/tzone/venv/bin/python -m tools.manage_platform create-company \
    --name "Example Ltd" --slug example --workspace "Example Group" \
    --owner-email owner@example.com --owner-name "Example Owner" \
    --owner-password "CHANGE_THIS_ON_FIRST_LOGIN"'
```

Write down the workspace code it prints. It is shown once.

## 5. Frontend build

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
cd /opt/tzone/frontend
sudo -u tzone npm ci
sudo -u tzone npx vite build
```

## 6. systemd

```bash
sudo cp /opt/tzone/deploy/tzone-api.service /etc/systemd/system/tzone-api.service
sudo systemctl daemon-reload
sudo systemctl enable --now tzone-api
sudo systemctl status tzone-api
```

## 7. nginx and TLS

```bash
sudo cp /opt/tzone/deploy/tzone-proxy.conf /etc/nginx/snippets/tzone-proxy.conf
sudo cp /opt/tzone/deploy/tzone-security-headers.conf /etc/nginx/snippets/tzone-security-headers.conf
sudo cp /opt/tzone/deploy/nginx.conf /etc/nginx/sites-available/tzone
sudo sed -i 's/app.example.com/YOUR.DOMAIN/g' /etc/nginx/sites-available/tzone
sudo ln -sf /etc/nginx/sites-available/tzone /etc/nginx/sites-enabled/tzone
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d YOUR.DOMAIN
sudo nginx -t && sudo systemctl reload nginx
```

## 8. Backups

```bash
sudo install -o root -g root -m 644 /opt/tzone/deploy/backup.cron /etc/cron.d/tzone-backup
sudo sed -i 's/ops@example.com/YOUR_OPS_EMAIL/' /etc/cron.d/tzone-backup
sudo -u tzone bash -c 'set -a; . /etc/tzone/tzone.env; set +a; cd /opt/tzone && \
  /opt/tzone/venv/bin/python -m tools.manage_platform backup --output /var/backups/tzone'
```

## 9. Verify

```bash
sudo -u tzone bash -c 'set -a; . /etc/tzone/tzone.env; set +a; cd /opt/tzone && \
  /opt/tzone/venv/bin/python -m tools.manage_platform check'
curl -sS https://YOUR.DOMAIN/health/
curl -sSN https://YOUR.DOMAIN/conversations/live/events --max-time 5   # must stream, not buffer
```

`check` must exit 0. Anything else means the deployment is not ready.

## Redeploy

```bash
cd /opt/tzone
sudo -u tzone git pull
sudo -u tzone /opt/tzone/venv/bin/pip install -r requirements.txt
sudo -u tzone bash -c 'cd frontend && npm ci && npx vite build'
sudo systemctl restart tzone-api
sudo -u tzone bash -c 'set -a; . /etc/tzone/tzone.env; set +a; cd /opt/tzone && \
  /opt/tzone/venv/bin/python -m tools.manage_platform check'
```

### Schema migrations run themselves

Restarting `tzone-api` runs any pending tenant-schema upgrade at startup
(`upgrade_outdated_tenants` in the app's lifespan), so `git pull` + `restart`
is all a schema change needs — there is no separate migration command. This
release's change is one such: the message-deduplication index moves from
`provider_message_id` alone to `(channel, external_user_id, provider_message_id)`
so two Telegram customers who share a per-chat message id are both kept. Each
company database picks it up on the first restart after the pull; the `check`
above then reports every company at the current schema version.

