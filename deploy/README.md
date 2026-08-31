# deploy/

## Fast path — one command does everything

Once the code is on the server (see below), run:

```bash
sudo bash /opt/tzone/deploy/install.sh
```

`install.sh` performs every step in this file for you -- system packages, the
service account, the Python environment, the master key, the environment file,
the first company, the frontend build, systemd, nginx, HTTPS and backups -- and
verifies the result. It asks only for your domain, an email for the TLS
certificate, and the first owner account, and it prints the master key and
workspace code at the end. It is safe to re-run (it never regenerates the master
key). The manual steps below are the same thing spelled out, for when you want
to do it by hand or adapt it.

To get the code on the server first:

```bash
sudo mkdir -p /opt/tzone && sudo chown "$USER" /opt/tzone
git clone https://github.com/tzonelb/tzone-assistant.git /opt/tzone
cd /opt/tzone && git checkout claude/platform-comprehensive-audit-ff7985
```

If the repository is private, git will ask for a username and password: use your
GitHub username and a Personal Access Token (github.com -> Settings -> Developer
settings -> Personal access tokens -> Fine-grained tokens -> Generate, with read
access to this repository) as the password.

---

# deploy/ (manual steps)

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

## 4b. Super Admin (platform operator)

The company owner above runs one company. The **Super Admin** operates the
platform itself — the `/superadmin` console where companies are created,
suspended, given plans, and have modules toggled. Nothing seeds it, so it must be
created once, or the console cannot be entered at all:

```bash
cd /opt/tzone
sudo -u tzone bash -c 'set -a; . /etc/tzone/tzone.env; set +a; \
  /opt/tzone/venv/bin/python -m tools.manage_platform create-super-admin \
    --email you@example.com --name "Your Name" --password "CHANGE_THIS_10_CHARS_MIN"'
```

Then sign in at `https://YOUR.DOMAIN/superadmin/login` — **no workspace code** —
and complete the mandatory 2FA (TOTP) enrolment on first login before any console
action works. Re-running with the same email just resets that password; it does
not create a second operator. (`install.sh` now does this step for you.)

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

## Redeploy — push to deploy, no console visit

After the installer runs, a systemd timer (`tzone-update.timer`) checks the
deployed branch every 3 minutes and, when it moves, runs
[`update.sh`](update.sh): pull → rebuild only what changed → restart →
health-check on `/health` → **roll back to the previous commit if the new version
is unhealthy**. So a normal deploy is just:

```bash
git push        # from your laptop, to the deployed branch
```

The server updates itself within a few minutes. You never open the server for a
code change. To watch or force it:

```bash
systemctl list-timers tzone-update.timer   # when the next check runs
tail -f /var/log/tzone-update.log          # what it did / rollbacks
sudo bash /opt/tzone/deploy/update.sh      # deploy right now instead of waiting
```

The updater pins `TZONE_DEPLOY_BRANCH` (in `/etc/tzone/tzone.env`) — only pushes
to that one branch deploy; a push to any other branch is ignored. It runs all
git/npm/pip work as the `tzone` user, so the "dubious ownership" error never
appears. systemd unit files are version-controlled too: change one and the
updater re-installs it on the next run, so even deploy-config changes ship with
no console.

### If the timer is not installed yet (older install)

```bash
cd /opt/tzone && sudo -u tzone git pull            # get deploy/update.sh + units
sudo cp deploy/tzone-update.service /etc/systemd/system/
sudo cp deploy/tzone-update.timer   /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now tzone-update.timer
```

### Making the GitHub repo private

Auto-deploy pulls over the network, so a private repo needs credentials on the
server. Use a **read-only deploy key** (scoped to this one repo, cannot push,
cannot reach your account) — run once *before* flipping the repo to private:

```bash
sudo bash /opt/tzone/deploy/setup-deploy-key.sh
```

It prints one public key to paste at
`github.com/tzonelb/tzone-assistant/settings/keys` (leave "Allow write access"
unchecked), switches the remote to SSH, and tells you how to test it. After that,
`git push` keeps auto-deploying exactly as before.

### Manual redeploy (fallback only)

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

