# Deploying T-ZONE to a VPS

This repo is set up to run as three Docker containers behind Caddy
(automatic HTTPS, no manual certificate setup):

- **backend** — FastAPI app (Gunicorn + Uvicorn workers)
- **web** — Caddy: serves the built frontend and reverse-proxies
  `/api/*` and `/uploads/*` to the backend, on the same domain
- SQLite database and uploaded media are stored in Docker volumes so
  they survive rebuilds/redeploys

## 1. Prerequisites

- A VPS (2GB+ RAM is plenty for this scale) running a recent Ubuntu/Debian
- A domain name, with an **A record pointing its IP at the VPS**
  (Caddy cannot get a TLS certificate until this resolves — check with
  `dig app.yourdomain.com` before starting)
- Docker + the Docker Compose plugin installed on the VPS:
  ```bash
  curl -fsSL https://get.docker.com | sh
  ```
- Ports 80 and 443 open in the VPS firewall

## 2. Get the code onto the VPS

```bash
git clone https://github.com/tzonelb/tzone-assistant.git
cd tzone-assistant
```

## 3. Configure

```bash
cp .env.production.example .env
nano .env
```

Fill in at minimum:
- `DOMAIN` and `VITE_API_BASE_URL` / `PUBLIC_BACKEND_URL` — your real domain, with `https://`
- `JWT_SECRET` — generate with `openssl rand -hex 32`, never use the default
- Whichever channel credentials you're connecting (WhatsApp/Telegram/Meta) — these can also be added later per-company from the app's Settings if you're using per-company channel connections instead of the platform-wide `.env` ones

## 4. Start it

```bash
docker compose up -d --build
```

First boot takes a minute or two (installing dependencies, building
the frontend, Caddy requesting the TLS certificate). Watch progress with:

```bash
docker compose logs -f
```

Once it settles, visit `https://yourdomain.com` — you should see the
login page.

## 5. Point your channel webhooks at the new domain

Once the domain is live, update the webhook URLs in each provider's
dashboard to `https://yourdomain.com/api/...` (WhatsApp Business
Platform, Meta App webhook subscriptions, Telegram bot webhook via
`setWebhook`). Local/dev webhook URLs won't work from those providers.

## Everyday operations

**Redeploy after pulling new code:**
```bash
git pull
docker compose up -d --build
```

**View logs:**
```bash
docker compose logs -f backend
docker compose logs -f web
```

**Back up the database and uploaded media:**
```bash
docker run --rm -v tzone-assistant_db-data:/data -v $(pwd):/backup alpine \
  tar czf /backup/db-backup-$(date +%F).tar.gz -C /data .
docker run --rm -v tzone-assistant_uploads-data:/data -v $(pwd):/backup alpine \
  tar czf /backup/uploads-backup-$(date +%F).tar.gz -C /data .
```
(Volume names are prefixed with the compose project name — check the
actual name with `docker volume ls` if the directory wasn't cloned as
`tzone-assistant`.)

**Restart just one service:**
```bash
docker compose restart backend
```

## Security hardening (do this before going live with real customer data)

### 1. Encrypt the disk (protects the SQLite database file at rest)
SQLCipher (application-level SQLite encryption) turned out to have real
Python-packaging problems on current Python versions — no prebuilt
wheel, and building from source needs extra system libraries that add
fragility for little benefit here. **Full-disk encryption solves the
same threat** (someone stealing the raw files off the server) with
zero application changes and zero risk to functionality — search,
filters, everything keeps working exactly as it does today, since
decryption happens transparently at the disk layer, not per-query.

Two ways to get it, pick whichever your VPS provider supports:
- **Provider-managed** (easiest): Hetzner, DigitalOcean, Vultr, and
  most others offer "encrypted volume" as a checkbox at server
  creation. If offered, just enable it — nothing else to configure.
- **LUKS at OS install time**: most Debian/Ubuntu installers offer
  "Encrypt the new file system" during setup. Must be chosen at
  install time — cannot be added to an already-running unencrypted
  disk without reinstalling.

### 2. Put Cloudflare in front (free — hides the real server, blocks attacks)
1. Add the domain to a Cloudflare account, point its nameservers there
   (Cloudflare walks you through this).
2. In DNS settings, make sure the A record has the **orange cloud
   (Proxied)** turned ON — this is what hides the VPS's real IP and
   activates Cloudflare's DDoS/WAF protection.
3. In SSL/TLS settings, set the mode to **"Full (strict)"** — this
   keeps the connection encrypted all the way from Cloudflare to
   Caddy too, not just from the visitor's browser to Cloudflare.
4. That's it — Caddy still gets its own Let's Encrypt certificate as
   before; Cloudflare just sits in front of it now.

### 3. Lock down SSH access to the VPS itself
```bash
# On your own machine, generate a key if you don't have one:
ssh-keygen -t ed25519

# Copy it to the VPS, then disable password login entirely:
ssh-copy-id root@your-vps-ip
# Edit /etc/ssh/sshd_config on the VPS:
#   PasswordAuthentication no
#   PermitRootLogin prohibit-password
sudo systemctl restart ssh
```
Also install `fail2ban` (auto-bans IPs after repeated failed SSH
attempts) and enable unattended security updates:
```bash
sudo apt install -y fail2ban unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

### 4. Already built into the app (no extra setup needed)
- **Login brute-force protection**: an account locks for 15 minutes
  after 5 wrong password attempts in a row (`backend/services/auth_service.py`).
- **Password hashing**: PBKDF2-SHA256 with 310,000 iterations — this
  already meets OWASP's current recommended minimum.
- **Channel credentials** (WhatsApp/Telegram/Meta tokens): already
  encrypted at rest in the database (`backend/services/crypto_utils.py`,
  Fernet symmetric encryption, key derived from `JWT_SECRET`).
- **Security response headers** (HSTS, X-Frame-Options, etc.) are set
  by Caddy automatically (`frontend/Caddyfile`).
- **Transport encryption**: HTTPS everywhere, automatic (Caddy + Let's
  Encrypt, reinforced by Cloudflare's "Full (strict)" mode above).

## Notes

- This deployment uses SQLite, which is fine at this scale (single
  VPS, moderate traffic) — there's no separate database server to
  manage or back up.
- Scaling the backend to more than one machine would need a real
  database (Postgres) instead, since SQLite doesn't support multiple
  writers across hosts. Not needed for a single-VPS deployment.
- Calls (`backend/services/call_log_service.py`) is a call *log*, not
  live telephony — actual dialing needs a separate Twilio (or similar)
  account, which is a business decision independent of this deploy.
