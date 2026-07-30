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
