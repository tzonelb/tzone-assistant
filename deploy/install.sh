#!/usr/bin/env bash
#
# T-ZONE one-command installer for a fresh Ubuntu/Debian VPS.
#
# It performs every step of docs/DEPLOYMENT.md for you: system packages, the
# service account, the Python environment, the master key, the environment
# file, the first company, the frontend build, systemd, nginx, HTTPS and
# backups -- then verifies the result. It asks only for the handful of values
# no script can invent (your domain, an email for the TLS certificate, and the
# first owner account), and it prints the master key and workspace code at the
# end with instructions to save them.
#
# Run it from the cloned repository, as root:
#     sudo bash /opt/tzone/deploy/install.sh
#
# It is safe to run again: every step checks whether it is already done.

set -euo pipefail

# --------------------------------------------------------------------------
# Pretty output
# --------------------------------------------------------------------------
BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; BLUE=$'\033[34m'; OFF=$'\033[0m'
step()  { echo; echo "${BOLD}${BLUE}==> $*${OFF}"; }
ok()    { echo "${GREEN}  [ok] $*${OFF}"; }
warn()  { echo "${YELLOW}  [!] $*${OFF}"; }
die()   { echo "${RED}${BOLD}  [x] $*${OFF}" >&2; exit 1; }
ask()   { local prompt="$1" def="${2:-}" ans; if [ -n "$def" ]; then read -rp "  $prompt [$def]: " ans; echo "${ans:-$def}"; else read -rp "  $prompt: " ans; echo "$ans"; fi; }

trap 'echo; die "Something failed at line $LINENO. Nothing above this point was lost. Fix the reported problem and run the script again -- it resumes where it can."' ERR

APP_DIR=/opt/tzone
ENV_FILE=/etc/tzone/tzone.env
SERVICE=tzone-api

echo "${BOLD}T-ZONE installer${OFF}"
echo "This sets the platform up end to end. It will ask you a few questions, then run on its own."

# --------------------------------------------------------------------------
# 0. Preconditions
# --------------------------------------------------------------------------
step "Checking the basics"
[ "$(id -u)" -eq 0 ] || die "Please run with sudo:  sudo bash $0"
command -v apt-get >/dev/null 2>&1 || die "This installer supports Ubuntu/Debian (apt). Your server uses something else -- tell the assistant and it will adapt."
SELF="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SELF/.." && pwd)"
[ -f "$REPO_ROOT/requirements.txt" ] || die "Could not find the repository. Run this script from inside the cloned repo (e.g. sudo bash /opt/tzone/deploy/install.sh)."
ok "Running as root on an apt-based system; repo found at $REPO_ROOT"

# Detect a managed control panel (CloudPanel/Plesk/cPanel). These own nginx and
# TLS, so we must NOT touch nginx or run certbot -- we install only the app on
# 127.0.0.1:8000 and let the panel reverse-proxy the domain onto it. This keeps
# any existing WordPress site completely untouched.
PANEL=""
if command -v clpctl >/dev/null 2>&1 || [ -d /home/clp ] || [ -d /etc/cloudpanel ]; then PANEL="CloudPanel"; fi
if [ -z "$PANEL" ] && [ -d /usr/local/psa ]; then PANEL="Plesk"; fi
if [ -z "$PANEL" ] && [ -d /usr/local/cpanel ]; then PANEL="cPanel"; fi
if [ -n "$PANEL" ]; then
  warn "Detected $PANEL on this server. The installer will set up the app and its"
  warn "service only, and NOT touch nginx or TLS -- you will point $PANEL at the app"
  warn "with a reverse-proxy site (instructions printed at the end). WordPress stays untouched."
fi

# --------------------------------------------------------------------------
# 1. Questions (everything the script cannot invent)
# --------------------------------------------------------------------------
step "A few questions"
DOMAIN="$(ask 'Your domain for the platform (e.g. app.yourcompany.com)')"
[ -n "$DOMAIN" ] || die "A domain is required."
TLS_EMAIL="$(ask 'Your email (for the HTTPS certificate and renewal notices)')"
[ -n "$TLS_EMAIL" ] || die "An email is required for the certificate."
COMPANY_NAME="$(ask 'First company name' 'My Company')"
COMPANY_SLUG="$(ask 'First company short code (letters/numbers only)' 'main')"
WORKSPACE_NAME="$(ask 'Workspace/group name' "$COMPANY_NAME Group")"
OWNER_NAME="$(ask 'Owner full name' 'Owner')"
OWNER_EMAIL="$(ask 'Owner login email')"
[ -n "$OWNER_EMAIL" ] || die "An owner email is required."
while :; do
  read -rsp "  Owner password (at least 10 characters, hidden): " OWNER_PW; echo
  [ "${#OWNER_PW}" -ge 10 ] && break
  warn "Too short -- use at least 10 characters."
done

echo
echo "  ${BOLD}Platform operator (Super Admin)${OFF} -- this is YOUR account for the"
echo "  control console at /superadmin, where you create and manage companies."
echo "  It is separate from the company owner above and needs no workspace code."
SUPER_EMAIL="$(ask 'Super Admin login email' "$OWNER_EMAIL")"
SUPER_NAME="$(ask 'Super Admin full name' "$OWNER_NAME")"
while :; do
  read -rsp "  Super Admin password (at least 10 characters, hidden): " SUPER_PW; echo
  [ "${#SUPER_PW}" -ge 10 ] && break
  warn "Too short -- use at least 10 characters."
done

echo
echo "${BOLD}About to install for domain:${OFF} $DOMAIN"
echo "${YELLOW}  Make sure this domain's DNS A record already points to THIS server's IP,${OFF}"
echo "${YELLOW}  or the HTTPS step will fail. (Current server IP: $(curl -fsS ifconfig.me 2>/dev/null || echo 'unknown'))${OFF}"
CONFIRM="$(ask 'Type yes to continue' 'yes')"
[ "$CONFIRM" = "yes" ] || die "Stopped at your request. Re-run when ready."

# --------------------------------------------------------------------------
# 2. System packages
# --------------------------------------------------------------------------
step "Installing system packages (this can take a couple of minutes)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y >/dev/null
if [ -n "$PANEL" ]; then
  apt-get install -y python3 python3-venv python3-pip git curl rsync >/dev/null
else
  apt-get install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx git curl rsync >/dev/null
fi
ok "System packages installed"

if ! command -v node >/dev/null 2>&1; then
  step "Installing Node.js 22 (for the web interface build)"
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null
  apt-get install -y nodejs >/dev/null
fi
ok "Node.js $(node --version) present"

# --------------------------------------------------------------------------
# 3. Service account and directories
# --------------------------------------------------------------------------
step "Creating the service account and directories"
id tzone >/dev/null 2>&1 || adduser --system --group --home "$APP_DIR" --disabled-login tzone
mkdir -p "$APP_DIR" /etc/tzone /var/backups/tzone /var/www/certbot
# Put the code at /opt/tzone if it is not already there.
if [ "$REPO_ROOT" != "$APP_DIR" ]; then
  rsync -a --delete --exclude data --exclude logs --exclude '.git' "$REPO_ROOT"/ "$APP_DIR"/
fi
mkdir -p "$APP_DIR/data" "$APP_DIR/logs"
chown -R tzone:tzone "$APP_DIR" /var/backups/tzone
ok "Code in place at $APP_DIR"

# --------------------------------------------------------------------------
# 4. Python environment
# --------------------------------------------------------------------------
step "Building the Python environment"
[ -d "$APP_DIR/venv" ] || sudo -u tzone python3 -m venv "$APP_DIR/venv"
sudo -u tzone "$APP_DIR/venv/bin/pip" install --upgrade pip >/dev/null
sudo -u tzone "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" >/dev/null
ok "Python dependencies installed"

# --------------------------------------------------------------------------
# 5. Environment file and master key
# --------------------------------------------------------------------------
step "Preparing the environment file and master key"
if [ ! -f "$ENV_FILE" ]; then
  cp "$APP_DIR/.env.example" "$ENV_FILE"
fi
set_env() { local k="$1" v="$2"; if grep -qE "^#*\s*${k}=" "$ENV_FILE"; then sed -i "s|^#*\s*${k}=.*|${k}=${v}|" "$ENV_FILE"; else echo "${k}=${v}" >> "$ENV_FILE"; fi; }
# The master key is generated ONCE and never regenerated. Overwriting it would
# make every existing company database and every backup permanently unreadable,
# so a re-run reuses whatever is already stored.
EXISTING_KEY="$(grep -E '^TZONE_MASTER_KEY=.+' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true)"
if [ -n "$EXISTING_KEY" ]; then
  MASTER_KEY="$EXISTING_KEY"
  ok "Reusing the master key already in $ENV_FILE (regenerating it would orphan existing data)"
else
  MASTER_KEY="$(sudo -u tzone "$APP_DIR/venv/bin/python" -m tools.manage_platform generate-master-key | grep -oE 'TZONE_MASTER_KEY=\S+' | head -1 | cut -d= -f2-)"
  set_env TZONE_MASTER_KEY "$MASTER_KEY"
fi
set_env APP_ENV production
# Explicit rather than relying on the APP_ENV-aware default: an install made
# before this line has `ENABLE_DOCS=true` sitting in its env file from the old
# .env.example, and the default cannot override a value that is set. A re-run
# is how that install gets corrected.
set_env ENABLE_DOCS false
set_env DATA_DIR "$APP_DIR/data"
set_env LOG_DIR "$APP_DIR/logs"
set_env APP_PUBLIC_URL "https://$DOMAIN"
set_env CORS_ORIGINS "https://$DOMAIN"
# Pin the branch the self-updater deploys. Only pushes to THIS branch reach the
# server; a push to any other branch is ignored. Detected from the checkout.
if [ -d "$APP_DIR/.git" ]; then
  DEPLOY_BRANCH="$(cd "$APP_DIR" && sudo -u tzone git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  [ -n "$DEPLOY_BRANCH" ] && [ "$DEPLOY_BRANCH" != "HEAD" ] && set_env TZONE_DEPLOY_BRANCH "$DEPLOY_BRANCH"
fi
chown root:tzone "$ENV_FILE"; chmod 640 "$ENV_FILE"
ok "Environment file written at $ENV_FILE (APP_ENV=production)"

# --------------------------------------------------------------------------
# 6. First company
# --------------------------------------------------------------------------
step "Creating the first company"
CREATE_OUT="$(cd "$APP_DIR" && sudo -u tzone bash -c "set -a; . '$ENV_FILE'; set +a; '$APP_DIR/venv/bin/python' -m tools.manage_platform create-company --name \"$COMPANY_NAME\" --slug \"$COMPANY_SLUG\" --workspace \"$WORKSPACE_NAME\" --owner-email \"$OWNER_EMAIL\" --owner-name \"$OWNER_NAME\" --owner-password \"$OWNER_PW\"" 2>&1 || true)"
echo "$CREATE_OUT" | sed 's/^/    /'
WORKSPACE_CODE="$(echo "$CREATE_OUT" | grep -oE 'TZ(-[A-Z0-9]{4})+' | head -1 || true)"
ok "First company created"

# --------------------------------------------------------------------------
# 6b. Super Admin (platform operator)
# --------------------------------------------------------------------------
# Without this account the /superadmin console cannot be entered at all, so the
# platform can never be operated. It is created idempotently -- re-running with the
# same email just resets that password, it does not make a second operator.
step "Creating the Super Admin (platform operator)"
SUPER_OUT="$(cd "$APP_DIR" && sudo -u tzone bash -c "set -a; . '$ENV_FILE'; set +a; '$APP_DIR/venv/bin/python' -m tools.manage_platform create-super-admin --email \"$SUPER_EMAIL\" --name \"$SUPER_NAME\" --password \"$SUPER_PW\"" 2>&1 || true)"
echo "$SUPER_OUT" | sed 's/^/    /'
echo "$SUPER_OUT" | grep -qiE 'super.?admin' || warn "Super Admin creation output looked unexpected -- verify with the console at /superadmin/login after install."
ok "Super Admin ready (log in at ${DOMAIN}/superadmin/login -- no workspace code; you will set up 2FA on first login)"

# --------------------------------------------------------------------------
# 7. Frontend build
# --------------------------------------------------------------------------
step "Building the web interface (a minute or two)"
cd "$APP_DIR/frontend"
sudo -u tzone npm ci >/dev/null 2>&1
sudo -u tzone npx vite build >/dev/null 2>&1
ok "Web interface built"

# --------------------------------------------------------------------------
# 8. systemd service
# --------------------------------------------------------------------------
step "Installing and starting the application service"
cp "$APP_DIR/deploy/tzone-api.service" /etc/systemd/system/${SERVICE}.service
systemctl daemon-reload
systemctl enable --now ${SERVICE} >/dev/null 2>&1
sleep 2
systemctl is-active --quiet ${SERVICE} || { journalctl -u ${SERVICE} -n 30 --no-pager; die "The service did not start -- the log above shows why."; }
ok "Service ${SERVICE} is running"

# --------------------------------------------------------------------------
# 9. nginx + HTTPS
# --------------------------------------------------------------------------
if [ -n "$PANEL" ]; then
  step "Web server: managed by $PANEL (skipping nginx and certbot)"
  ok "The app is serving on http://127.0.0.1:8000. $PANEL will publish it on your domain."
  URL="https://$DOMAIN"
  PANEL_REVERSE_PROXY=1
else
  step "Configuring nginx and requesting the HTTPS certificate"
  cp "$APP_DIR/deploy/tzone-proxy.conf" /etc/nginx/snippets/tzone-proxy.conf
  cp "$APP_DIR/deploy/tzone-security-headers.conf" /etc/nginx/snippets/tzone-security-headers.conf
  cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/tzone
  sed -i "s/app.example.com/$DOMAIN/g" /etc/nginx/sites-available/tzone
  ln -sf /etc/nginx/sites-available/tzone /etc/nginx/sites-enabled/tzone
  # Coexistence: only remove nginx's stock default site when this box serves
  # nothing else. If other sites are enabled (e.g. a WordPress vhost), leave every
  # one of them alone -- nginx routes by server_name, so T-ZONE answers only for
  # $DOMAIN and the other sites keep their own domains.
  OTHER_SITES="$(ls /etc/nginx/sites-enabled/ 2>/dev/null | grep -vx tzone | grep -vx default || true)"
  if [ -z "$OTHER_SITES" ] && [ -e /etc/nginx/sites-enabled/default ]; then
    rm -f /etc/nginx/sites-enabled/default
  else
    [ -n "$OTHER_SITES" ] && warn "Other nginx sites are enabled and were left untouched: $OTHER_SITES"
  fi
  if systemctl is-active --quiet apache2 2>/dev/null; then
    warn "Apache is running and will fight nginx for ports 80/443. If your WordPress is on Apache, do not let this script bind those ports -- stop here and tell the assistant so it can set up a coexistence proxy instead."
  fi
  nginx -t && systemctl reload nginx
  if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$TLS_EMAIL" --redirect >/dev/null 2>&1; then
    nginx -t && systemctl reload nginx
    ok "HTTPS certificate installed for $DOMAIN"
    URL="https://$DOMAIN"
  else
    warn "The certificate could not be issued automatically. This almost always means the domain's DNS is not yet pointing at this server, or port 80/443 is blocked."
    warn "The site is running on http for now. Once DNS is correct, run:  sudo certbot --nginx -d $DOMAIN"
    URL="http://$DOMAIN"
  fi
fi

# --------------------------------------------------------------------------
# 10. Backups
# --------------------------------------------------------------------------
step "Enabling scheduled backups"
install -o root -g root -m 644 "$APP_DIR/deploy/backup.cron" /etc/cron.d/tzone-backup
sed -i "s/ops@example.com/$TLS_EMAIL/" /etc/cron.d/tzone-backup
cd "$APP_DIR" && sudo -u tzone bash -c "set -a; . '$ENV_FILE'; set +a; '$APP_DIR/venv/bin/python' -m tools.manage_platform backup --output /var/backups/tzone" >/dev/null 2>&1 || warn "First backup could not run now; the nightly job will still run."
ok "Backups scheduled"

# --------------------------------------------------------------------------
# 10b. Automatic updates (deploy by pushing to GitHub -- no console visit)
# --------------------------------------------------------------------------
# A systemd timer polls the deployed branch every few minutes and, when it moves,
# runs deploy/update.sh: pull, rebuild only what changed, restart, health-check,
# and roll back if the new version is unhealthy. After this, shipping a change is
# just "git push" -- you never open this server again.
step "Enabling automatic updates (push to deploy)"
if [ -d "$APP_DIR/.git" ]; then
  cp "$APP_DIR/deploy/tzone-update.service" /etc/systemd/system/tzone-update.service
  cp "$APP_DIR/deploy/tzone-update.timer"   /etc/systemd/system/tzone-update.timer
  systemctl daemon-reload
  systemctl enable --now tzone-update.timer >/dev/null 2>&1
  ok "Auto-update timer active (checks ${TZONE_DEPLOY_BRANCH:-the deployed branch} every 3 min). Deploy by pushing to GitHub."
else
  warn "No git checkout at $APP_DIR, so auto-update is off. To enable it, install from a clone at $APP_DIR (git clone ... $APP_DIR) and re-run."
fi

# --------------------------------------------------------------------------
# 11. Verify
# --------------------------------------------------------------------------
step "Final verification"
cd "$APP_DIR" && sudo -u tzone bash -c "set -a; . '$ENV_FILE'; set +a; '$APP_DIR/venv/bin/python' -m tools.manage_platform check" || die "The platform self-check failed -- see the messages above."

echo
echo "${BOLD}${GREEN}============================================================${OFF}"
echo "${BOLD}${GREEN}  T-ZONE is installed and running.${OFF}"
echo "${BOLD}${GREEN}============================================================${OFF}"
echo
echo "  ${BOLD}Open your platform:${OFF}   $URL"
echo "  ${BOLD}Owner login email:${OFF}   $OWNER_EMAIL"
echo "  ${BOLD}Owner password:${OFF}      (the one you typed)"
if [ -n "${WORKSPACE_CODE:-}" ]; then
  echo "  ${BOLD}Workspace code:${OFF}      ${YELLOW}${WORKSPACE_CODE}${OFF}   <- you need this to log in"
else
  echo "  ${BOLD}Workspace code:${OFF}      see the 'First company' output above (shown once)"
fi
echo
echo "  ${BOLD}Platform console:${OFF}    $URL/superadmin/login   (this is where you manage companies)"
echo "  ${BOLD}Super Admin email:${OFF}   $SUPER_EMAIL   (no workspace code; you set up 2FA on first login)"
echo "  ${BOLD}Super Admin password:${OFF} (the one you typed)"
echo
echo "${RED}${BOLD}  SAVE THESE TWO THINGS SOMEWHERE SAFE AND OFFLINE, NOW:${OFF}"
echo "${RED}    1) The workspace code above.${OFF}"
echo "${RED}    2) The master key below. Without it, every company database and every"
echo "       backup is unreadable forever. It is stored on this server, but keep a"
echo "       second copy off the server.${OFF}"
echo
echo "  ${BOLD}TZONE_MASTER_KEY${OFF} = ${YELLOW}${MASTER_KEY}${OFF}"
echo
if [ -n "${PANEL_REVERSE_PROXY:-}" ]; then
  echo
  echo "${BOLD}${YELLOW}  ONE STEP LEFT -- publish the app through $PANEL:${OFF}"
  echo "  The app is running privately on http://127.0.0.1:8000. In the $PANEL UI,"
  echo "  add a site for ${BOLD}$DOMAIN${OFF} of type ${BOLD}Reverse Proxy${OFF} pointing to"
  echo "  ${BOLD}http://127.0.0.1:8000${OFF}, then turn on Let's Encrypt SSL for it."
  echo "  In CloudPanel: Sites -> Add Site -> Create a Reverse Proxy -> Domain=$DOMAIN,"
  echo "  Reverse Proxy URL=http://127.0.0.1:8000 ; then the site's SSL/TLS tab ->"
  echo "  'New Let's Encrypt Certificate'. WordPress is not affected."
  echo "  Make sure $DOMAIN's DNS A record points to this server first."
  echo
fi
echo "  ${BOLD}${GREEN}Updates from here on: just 'git push' to ${TZONE_DEPLOY_BRANCH:-your branch}.${OFF}"
echo "  The server checks every 3 minutes and deploys itself -- you never open this console again."
echo
echo "  Useful later (you should not need these):"
echo "    systemctl status $SERVICE                  # is the app running"
echo "    journalctl -u $SERVICE -f                  # live app logs"
echo "    systemctl list-timers tzone-update.timer   # when the next auto-update check runs"
echo "    tail -f /var/log/tzone-update.log          # what the auto-updater did"
echo "    sudo bash $APP_DIR/deploy/update.sh        # deploy right now instead of waiting"
echo
