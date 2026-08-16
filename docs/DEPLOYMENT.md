# Deployment Runbook

Production deployment of the T-ZONE Platform on a single Ubuntu VPS.

Target layout:

| Path | Contents |
| --- | --- |
| `/opt/tzone` | application code, virtualenv, built frontend |
| `/opt/tzone/data` | `control.db` and `tenants/company_<id>.db` — every encrypted database |
| `/opt/tzone/logs` | application and backup logs |
| `/etc/tzone/tzone.env` | environment, including the master key |
| `/var/backups/tzone` | nightly backups |

Companion files live in [`deploy/`](../deploy/README.md): the systemd unit,
the nginx site, the proxy snippet and the backup cron entry.

---

## 0. Read this before you start

> ### MASTER KEY CUSTODY
>
> Every company on this platform has its own database file, encrypted with its
> own key. Those keys are sealed under a single value: `TZONE_MASTER_KEY`.
>
> **If that value is lost, every company database on this server becomes
> permanently unreadable — and so does every backup, because the backups are
> copies of those same encrypted files.** There is no recovery procedure, no
> vendor override and no support ticket that can undo it. The encryption is
> doing exactly what it was chosen to do.
>
> Therefore:
>
> * Generate the key once, at first deployment. Never regenerate it for a
>   platform that already holds data — a new key cannot unwrap keys sealed
>   under the old one.
> * Keep at least one copy **off this server**: a password manager entry, a
>   sealed envelope in a safe, an offline vault. Two copies in two places is
>   the working minimum.
> * Keep that copy **separate from the backups**. Storing the key alongside
>   the encrypted files hands an attacker both halves at once, and loses both
>   halves at once.
> * Restrict `/etc/tzone/tzone.env` to `root:tzone` mode `640`. It is the only
>   file on the machine that turns the databases back into data.
> * When someone with access to the key leaves, rotating it is a project, not
>   a command. Plan for it rather than discovering it.

Workspace codes have the same one-way property, on a smaller scale: a code is
displayed once at creation, and if it is lost the only remedy is to issue a new
one with `rotate-workspace-code`.

---

## 1. Provision the VPS

Ubuntu 22.04 or 24.04. Minimum 2 vCPU / 2 GB RAM / 20 GB disk.

```bash
sudo apt update && sudo apt upgrade -y
sudo timedatectl set-timezone UTC
sudo hostnamectl set-hostname tzone-prod

sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

The API binds to `127.0.0.1:8000` and is only reachable through nginx, so
ports 80 and 443 are the only ones that need to be open.

Point an A record for your domain at the server's IP before requesting a
certificate in step 8, or the ACME challenge fails.

---

## 2. Install dependencies

```bash
sudo apt install -y python3 python3-venv python3-pip nginx certbot \
    python3-certbot-nginx git curl
```

**SQLCipher note.** Do not install a system SQLCipher package. The encrypted
databases are opened through the `sqlcipher3-binary` wheel pinned in
`requirements.txt`, which ships its own SQLCipher build. That is why no
`libsqlcipher-dev` package and no compiler are needed here — and it is also why
`pip install sqlcipher3` (without `-binary`) is the wrong package: it tries to
compile against a system library that is not present.

Node is only needed to build the frontend:

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
```

---

## 3. Service account and code

```bash
sudo adduser --system --group --home /opt/tzone --disabled-login tzone
sudo mkdir -p /opt/tzone /etc/tzone /var/backups/tzone /var/www/certbot
sudo chown -R tzone:tzone /opt/tzone /var/backups/tzone

sudo -u tzone git clone <REPO_URL> /opt/tzone
sudo -u tzone python3 -m venv /opt/tzone/venv
sudo -u tzone /opt/tzone/venv/bin/pip install --upgrade pip
sudo -u tzone /opt/tzone/venv/bin/pip install -r /opt/tzone/requirements.txt
sudo -u tzone mkdir -p /opt/tzone/data /opt/tzone/logs
```

The application never runs as root. The systemd unit hardening in step 7
depends on `/opt/tzone/data` and `/opt/tzone/logs` being writable by `tzone`
and on everything else being read-only.

---

## 4. Generate and store the master key

```bash
cd /opt/tzone
sudo -u tzone /opt/tzone/venv/bin/python -m tools.manage_platform generate-master-key
```

The command prints a line like `TZONE_MASTER_KEY=<base64>` and a warning. Do
both of these before continuing:

1. Copy the value into your off-server vault (see §0).
2. Put it in the environment file.

```bash
sudo cp /opt/tzone/.env.example /etc/tzone/tzone.env
sudo chown root:tzone /etc/tzone/tzone.env
sudo chmod 640 /etc/tzone/tzone.env
sudo nano /etc/tzone/tzone.env
```

At minimum set, for production:

```ini
APP_ENV=production
DEBUG=false
ENABLE_DOCS=false
TZONE_MASTER_KEY=<the value you just generated>
META_APP_SECRET=<from the Meta app dashboard, step 9>
DATA_DIR=/opt/tzone/data
LOG_DIR=/opt/tzone/logs
CORS_ORIGINS=https://YOUR.DOMAIN
ALLOW_UNSIGNED_WEBHOOKS=false
```

`DATA_DIR` and `LOG_DIR` must match the `ReadWritePaths` lines in the systemd
unit. Every other variable is documented inline in `.env.example`.

Confirm the environment is usable before going further:

```bash
sudo -u tzone bash -c 'set -a; . /etc/tzone/tzone.env; set +a; cd /opt/tzone && \
  /opt/tzone/venv/bin/python -m tools.manage_platform check'
```

On a fresh server this creates `control.db` and should end in
`PREFLIGHT PASSED`.

---

## 5. Create the first company

```bash
cd /opt/tzone
sudo -u tzone bash -c 'set -a; . /etc/tzone/tzone.env; set +a; \
  /opt/tzone/venv/bin/python -m tools.manage_platform create-company \
    --name "Example Ltd" \
    --slug example \
    --workspace "Example Group" \
    --owner-email owner@example.com \
    --owner-name "Example Owner" \
    --owner-password "a-strong-initial-password"'
```

This creates the workspace (if new), the company row, the company's own
encrypted database, the four default roles with their permissions, and the
owner user assigned to the owner role.

**It prints a workspace code exactly once.** Copy it out of the terminal now
and hand it to the company through a channel you trust. The code seals a second
copy of that company's database key; it is not stored anywhere in readable
form, so it cannot be shown again — not from the database, not from a backup,
not by support. If it is lost:

```bash
sudo -u tzone bash -c 'set -a; . /etc/tzone/tzone.env; set +a; cd /opt/tzone && \
  /opt/tzone/venv/bin/python -m tools.manage_platform rotate-workspace-code --company-id 1'
```

Rotation issues a new code immediately, invalidates the old one, and does not
re-encrypt or interrupt anything.

A platform super admin, who can reach every company, is created separately:

```bash
sudo -u tzone bash -c 'set -a; . /etc/tzone/tzone.env; set +a; cd /opt/tzone && \
  /opt/tzone/venv/bin/python -m tools.manage_platform create-super-admin \
    --email admin@yourcompany.com --name "Platform Admin" --password "a-strong-password"'
```

---

## 6. Build the frontend

```bash
cd /opt/tzone/frontend
sudo -u tzone npm ci
sudo -u tzone npx vite build
```

Output lands in `/opt/tzone/frontend/dist`, which is what nginx serves. Rebuild
after every code update; nginx serves whatever is on disk and will happily keep
serving a stale bundle.

---

## 7. Install the systemd unit

```bash
sudo cp /opt/tzone/deploy/tzone-api.service /etc/systemd/system/tzone-api.service
sudo systemctl daemon-reload
sudo systemctl enable --now tzone-api
sudo systemctl status tzone-api
journalctl -u tzone-api -f
```

**The unit runs uvicorn with `--workers 1`, deliberately.** The application
keeps per-conversation state in process memory — the message collection buffer,
the AI/human takeover timers, and the list of connected SSE clients. A second
worker splits that state across processes: buffered messages get answered twice
or not at all, an agent's takeover in one worker does not stop the AI replying
from the other, and dashboards connected to one worker stop receiving events
produced by the other. This is a correctness constraint, not a performance
setting. Raising it requires moving that state into the database first. Until
then, scale by making the machine bigger.

Confirm the API is up locally before putting nginx in front of it:

```bash
curl -sS http://127.0.0.1:8000/health/
```

---

## 8. nginx and TLS

```bash
sudo cp /opt/tzone/deploy/tzone-proxy.conf /etc/nginx/snippets/tzone-proxy.conf
sudo cp /opt/tzone/deploy/tzone-security-headers.conf /etc/nginx/snippets/tzone-security-headers.conf
sudo cp /opt/tzone/deploy/nginx.conf /etc/nginx/sites-available/tzone
sudo sed -i 's/app.example.com/YOUR.DOMAIN/g' /etc/nginx/sites-available/tzone
sudo ln -sf /etc/nginx/sites-available/tzone /etc/nginx/sites-enabled/tzone
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Then obtain the certificate:

```bash
sudo certbot --nginx -d YOUR.DOMAIN
sudo nginx -t && sudo systemctl reload nginx
sudo systemctl status certbot.timer     # renewal runs from this timer
```

The site config serves `frontend/dist` and proxies `/api`, `/conversations`,
`/webhook`, `/health`, `/knowledge` and `/tickets` to the API.

**Do not merge or reorder the `/conversations/live/events` location block.**
The dashboard holds an `EventSource` open against it for live inbox updates.
With nginx's defaults that endpoint appears to work and then quietly breaks:
response buffering holds each event until a buffer fills, and the default 60
second read timeout cuts the stream once a minute so the browser reconnects in
a loop. The dedicated block sets `proxy_buffering off`, `proxy_read_timeout
3600s` and HTTP/1.1 for exactly that reason.

---

## 9. Meta / WhatsApp webhooks

In the Meta app dashboard (**Settings → Basic**), copy the **App Secret** into
`META_APP_SECRET` in `/etc/tzone/tzone.env`, then restart the API:

```bash
sudo systemctl restart tzone-api
```

The app secret verifies the `X-Hub-Signature-256` header on every inbound
webhook. Without it, anyone who learns your webhook URL can inject messages
that look like they came from your customers. `ALLOW_UNSIGNED_WEBHOOKS` exists
only for local testing and must stay `false` here.

Configure the callback URLs in the Meta app, using the verify token you set in
`/etc/tzone/tzone.env`:

| Product | Callback URL | Verify token | Subscribe to |
| --- | --- | --- | --- |
| WhatsApp | `https://YOUR.DOMAIN/webhook/whatsapp/` | `WHATSAPP_VERIFY_TOKEN` | `messages` |
| Messenger | `https://YOUR.DOMAIN/webhook/messenger/` | `META_VERIFY_TOKEN` | `messages`, `messaging_postbacks` |
| Instagram | `https://YOUR.DOMAIN/webhook/instagram/` | `META_VERIFY_TOKEN` | `messages` |

Meta requires a publicly reachable HTTPS URL with a valid certificate, so this
step comes after step 8. When you click **Verify and save**, Meta sends a GET
with `hub.challenge`; watch it arrive:

```bash
journalctl -u tzone-api -f
```

Per-company channel credentials (page IDs, phone number IDs, access tokens) are
added from the dashboard afterwards. They are stored encrypted in the control
database, and they are what routes an inbound message to the right company —
the platform deliberately refuses to guess a company when nothing matches.

---

## 10. Backups

```bash
sudo install -o root -g root -m 644 /opt/tzone/deploy/backup.cron /etc/cron.d/tzone-backup
sudo sed -i 's/ops@example.com/YOUR_OPS_EMAIL/' /etc/cron.d/tzone-backup
```

The cron entry runs the CLI backup nightly at 03:15 into `/var/backups/tzone`
and deletes folders older than 30 days at 03:45. Run one by hand now so you
know it works:

```bash
sudo -u tzone bash -c 'set -a; . /etc/tzone/tzone.env; set +a; cd /opt/tzone && \
  /opt/tzone/venv/bin/python -m tools.manage_platform backup --output /var/backups/tzone'
```

Each run creates `/var/backups/tzone/tzone-backup-YYYYmmdd-HHMMSS/` containing
`control.db` and one `company_<id>.db` per company. The files are copied
exactly as they are on disk, still encrypted, so shipping them off-site is
safe.

Ship them off-site — a backup that only exists on the machine it backs up is
not a backup. And ship them to somewhere that does **not** hold the master key.

---

## 11. Verify the deployment

```bash
sudo -u tzone bash -c 'set -a; . /etc/tzone/tzone.env; set +a; cd /opt/tzone && \
  /opt/tzone/venv/bin/python -m tools.manage_platform check'
```

This is the command to run after every deploy. It verifies that the master key
is present and valid, that the data directory is writable by the service user,
that the control database opens and decrypts, and that every registered
company's database opens and decrypts. It exits non-zero if any of that fails,
so it can be wired into a deploy script directly.

Then check the surface:

```bash
curl -sS https://YOUR.DOMAIN/health/
curl -sSI https://YOUR.DOMAIN/ | grep -i strict-transport-security
curl -sSN https://YOUR.DOMAIN/conversations/live/events --max-time 5
sudo -u tzone bash -c 'set -a; . /etc/tzone/tzone.env; set +a; cd /opt/tzone && \
  /opt/tzone/venv/bin/python -m tools.manage_platform list-companies'
```

The SSE request must stream and stay open, not return all at once at the end.
If it buffers, the nginx location block from step 8 is not being matched.

---

## 12. Restore from a backup

You need two things: the backup folder, and the `TZONE_MASTER_KEY` that was in
use when it was taken. A backup without its key cannot be restored by anyone,
including you.

```bash
# 1. Stop the API so nothing writes while files are being replaced.
sudo systemctl stop tzone-api

# 2. Keep the current state aside rather than deleting it.
sudo -u tzone mv /opt/tzone/data /opt/tzone/data.broken-$(date +%Y%m%d-%H%M%S)
sudo -u tzone mkdir -p /opt/tzone/data/tenants

# 3. Copy the files back. control.db at the top, company files under tenants/.
BACKUP=/var/backups/tzone/tzone-backup-YYYYmmdd-HHMMSS
sudo -u tzone cp "$BACKUP"/control.db      /opt/tzone/data/
sudo -u tzone cp "$BACKUP"/company_*.db    /opt/tzone/data/tenants/

# 4. Make sure /etc/tzone/tzone.env holds the SAME master key as when the
#    backup was taken. A different key cannot decrypt these files.

# 5. Verify before starting anything.
sudo -u tzone bash -c 'set -a; . /etc/tzone/tzone.env; set +a; cd /opt/tzone && \
  /opt/tzone/venv/bin/python -m tools.manage_platform check'

# 6. Start the API.
sudo systemctl start tzone-api
```

If `check` reports `control database: file is not a database`, the master key
in the environment is not the one the backup was created under. Do not
overwrite anything further and do not generate a new key; find the correct key.

Workspace codes survive a restore unchanged — they are sealed inside
`control.db` — so employees keep using the codes they already have.

Once `check` passes and the dashboard loads, remove the `data.broken-*`
directory.

---

## Routine operations

| Task | Command |
| --- | --- |
| Restart the API | `sudo systemctl restart tzone-api` |
| Live logs | `journalctl -u tzone-api -f` |
| List companies and database sizes | `... manage_platform list-companies` |
| New workspace code for a company | `... manage_platform rotate-workspace-code --company-id N` |
| Add a company | `... manage_platform create-company --help` |
| Backup now | `... manage_platform backup --output /var/backups/tzone` |
| Post-deploy verification | `... manage_platform check` |

All CLI commands except `generate-master-key` need `TZONE_MASTER_KEY` in the
environment, which is why every example loads `/etc/tzone/tzone.env` first.
