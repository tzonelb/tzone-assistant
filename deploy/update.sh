#!/usr/bin/env bash
#
# T-ZONE self-updater.
#
# Pulls the deployed branch, rebuilds only what changed, restarts the service,
# and verifies it came back healthy -- rolling back to the previous commit if it
# did not. This is what lets you ship a change by pushing to GitHub, without ever
# opening the server console.
#
# It is invoked two ways:
#   * automatically, every few minutes, by the systemd timer tzone-update.timer
#   * by hand, when you want to deploy immediately:  sudo bash /opt/tzone/deploy/update.sh
#
# It does nothing (and says so) when the branch has not moved, so running it on a
# timer is cheap and safe. All git operations run as the code's owner (the tzone
# user), which is why the "dubious ownership" error never appears here.

set -euo pipefail

APP_DIR=/opt/tzone
ENV_FILE=/etc/tzone/tzone.env
SERVICE=tzone-api
LOG=/var/log/tzone-update.log
RUN_AS=tzone

# --- logging -------------------------------------------------------------
log() { echo "$(date '+%Y-%m-%d %H:%M:%S')  $*" | tee -a "$LOG" >&2; }
die() { log "ERROR: $*"; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run with sudo (needs to restart the service)"
[ -d "$APP_DIR/.git" ] || die "no git checkout at $APP_DIR"
mkdir -p "$(dirname "$LOG")"

# Never let two updates overlap (the timer could fire while a manual run is going).
exec 9>/var/lock/tzone-update.lock
flock -n 9 || { log "another update is already running; skipping"; exit 0; }

# git must trust the directory even when this runs as root against a tzone-owned tree.
git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true

as_owner() { sudo -u "$RUN_AS" -H bash -c "$1"; }

# --- which branch do we deploy? -----------------------------------------
# Pinned in the env file (TZONE_DEPLOY_BRANCH). Falls back to whatever branch is
# currently checked out. We only ever deploy this one branch, so a push to any
# other branch can never reach the server.
BRANCH="$(grep -E '^TZONE_DEPLOY_BRANCH=.+' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true)"
[ -n "$BRANCH" ] || BRANCH="$(as_owner "cd '$APP_DIR' && git rev-parse --abbrev-ref HEAD")"
[ -n "$BRANCH" ] || die "could not determine the branch to deploy"

# --- has anything changed? ----------------------------------------------
as_owner "cd '$APP_DIR' && git fetch --quiet origin '$BRANCH'" || die "git fetch failed (network or credentials)"
LOCAL="$(as_owner "cd '$APP_DIR' && git rev-parse HEAD")"
REMOTE="$(as_owner "cd '$APP_DIR' && git rev-parse 'origin/$BRANCH'")"

if [ "$LOCAL" = "$REMOTE" ]; then
  # Quiet on the timer; only the log records the check.
  echo "$(date '+%Y-%m-%d %H:%M:%S')  up to date on $BRANCH ($LOCAL)" >> "$LOG"
  exit 0
fi

log "updating $BRANCH:  $LOCAL -> $REMOTE"
PREV="$LOCAL"   # for rollback

# --- what kind of change is it? -----------------------------------------
CHANGED="$(as_owner "cd '$APP_DIR' && git diff --name-only '$LOCAL' '$REMOTE'")"
needs() { echo "$CHANGED" | grep -qE "$1"; }

# --- pull ----------------------------------------------------------------
as_owner "cd '$APP_DIR' && git checkout --quiet '$BRANCH' && git reset --hard 'origin/$BRANCH'" \
  || die "git update failed"

# --- rebuild only what changed ------------------------------------------
if needs '^requirements\.txt$'; then
  log "requirements.txt changed -> installing Python deps"
  as_owner "'$APP_DIR/venv/bin/pip' install -q -r '$APP_DIR/requirements.txt'" || die "pip install failed"
fi

# systemd units are version-controlled too, so a change to how the service or the
# updater itself runs deploys with no console visit: copy the units into place and
# reload. (The running update.sh is re-read from disk on the next timer tick, so a
# change to this very file also takes effect automatically next run.)
if needs '^deploy/tzone-.*\.(service|timer)$'; then
  log "systemd units changed -> reinstalling them"
  cp "$APP_DIR/deploy/tzone-api.service"    /etc/systemd/system/tzone-api.service
  cp "$APP_DIR/deploy/tzone-update.service" /etc/systemd/system/tzone-update.service
  cp "$APP_DIR/deploy/tzone-update.timer"   /etc/systemd/system/tzone-update.timer
  systemctl daemon-reload
  systemctl enable --now tzone-update.timer >/dev/null 2>&1 || true
fi

if needs '^frontend/'; then
  log "frontend changed -> rebuilding the web interface"
  as_owner "cd '$APP_DIR/frontend' && (npm ci --silent || npm install --silent)" || die "npm install failed"
  as_owner "cd '$APP_DIR/frontend' && npx vite build" >/dev/null 2>&1 || die "vite build failed"
fi

# --- restart (tenant-schema migrations run in the app's own startup) -----
log "restarting $SERVICE"
systemctl restart "$SERVICE"

# --- health check --------------------------------------------------------
healthy() {
  for _ in $(seq 1 15); do
    if curl -fsS -o /dev/null http://127.0.0.1:8000/health 2>/dev/null; then return 0; fi
    sleep 1
  done
  return 1
}

if healthy; then
  log "OK: $SERVICE healthy on $BRANCH @ $REMOTE"
  exit 0
fi

# --- rollback ------------------------------------------------------------
log "NEW VERSION FAILED ITS HEALTH CHECK -- rolling back to $PREV"
as_owner "cd '$APP_DIR' && git reset --hard '$PREV'" || die "rollback checkout failed (service is down)"
if echo "$CHANGED" | grep -qE '^requirements\.txt$'; then
  as_owner "'$APP_DIR/venv/bin/pip' install -q -r '$APP_DIR/requirements.txt'" || true
fi
if echo "$CHANGED" | grep -qE '^frontend/'; then
  as_owner "cd '$APP_DIR/frontend' && npx vite build" >/dev/null 2>&1 || true
fi
systemctl restart "$SERVICE"
if healthy; then
  log "rolled back to $PREV; service healthy again. The bad commit was NOT deployed."
  die "update aborted: $REMOTE failed its health check and was rolled back"
else
  die "update failed AND rollback did not restore health. Service is down -- investigate now: journalctl -u $SERVICE -n 50"
fi
