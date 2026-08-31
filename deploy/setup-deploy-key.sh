#!/usr/bin/env bash
#
# Prepare the server to pull a PRIVATE GitHub repository over SSH, using a
# read-only deploy key. Run this ONCE, before you flip the repo to private on
# GitHub. After it, `git pull` and the auto-updater keep working with no password
# and no personal token on the box.
#
#   sudo bash /opt/tzone/deploy/setup-deploy-key.sh
#
# A deploy key is the safe choice here: it is scoped to this one repository and
# read-only, so even if the server were compromised the key cannot push, cannot
# touch your other repos, and cannot touch your GitHub account.

set -euo pipefail

APP_DIR=/opt/tzone
RUN_AS=tzone
SSH_DIR="$APP_DIR/.ssh"
KEY="$SSH_DIR/tzone_deploy"
REMOTE_SSH="git@github.com:tzonelb/tzone-assistant.git"

BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; OFF=$'\033[0m'
die() { echo "${RED}${BOLD}[x] $*${OFF}" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run with sudo: sudo bash $0"
[ -d "$APP_DIR/.git" ] || die "no git checkout at $APP_DIR"
command -v ssh-keygen >/dev/null || { apt-get update -y >/dev/null && apt-get install -y openssh-client >/dev/null; }

as_owner() { sudo -u "$RUN_AS" -H bash -c "$1"; }

# 1. A dedicated key for this deploy (generated once, no passphrase so the timer
#    can use it unattended). It never leaves this server.
as_owner "mkdir -p '$SSH_DIR' && chmod 700 '$SSH_DIR'"
if [ ! -f "$KEY" ]; then
  as_owner "ssh-keygen -t ed25519 -N '' -C 'tzone-deploy@$(hostname)' -f '$KEY' >/dev/null"
  echo "${GREEN}[ok] generated a new deploy key at $KEY${OFF}"
else
  echo "${YELLOW}[!] reusing the existing deploy key at $KEY${OFF}"
fi

# 2. Tell git (for the tzone user) to use this key for GitHub, and pin GitHub's
#    host key so the first unattended pull does not stall on a prompt.
as_owner "ssh-keygen -F github.com -f '$SSH_DIR/known_hosts' >/dev/null 2>&1 || ssh-keyscan -t ed25519 github.com >> '$SSH_DIR/known_hosts' 2>/dev/null"
as_owner "cd '$APP_DIR' && git config core.sshCommand 'ssh -i $KEY -o UserKnownHostsFile=$SSH_DIR/known_hosts -o IdentitiesOnly=yes'"

# 3. Point origin at the SSH URL (was HTTPS while the repo was public).
as_owner "cd '$APP_DIR' && git remote set-url origin '$REMOTE_SSH'"

echo
echo "${BOLD}============================================================${OFF}"
echo "${BOLD} Add this ONE key to GitHub, then you are done.${OFF}"
echo "${BOLD}============================================================${OFF}"
echo
echo "  1. Copy the line below (the PUBLIC key -- safe to share):"
echo
echo "${GREEN}$(cat "$KEY.pub")${OFF}"
echo
echo "  2. On GitHub open:"
echo "       https://github.com/tzonelb/tzone-assistant/settings/keys"
echo "     -> 'Add deploy key'. Title: this server's name. Paste the key."
echo "     -> LEAVE 'Allow write access' UNCHECKED (read-only is what we want)."
echo "     -> Add key."
echo
echo "  3. Come back here and test it:"
echo "${BOLD}       sudo -u tzone git -C $APP_DIR fetch origin${OFF}"
echo "     If that succeeds with no password prompt, you can flip the repo to"
echo "     Private on GitHub and every future 'git push' will still auto-deploy."
echo
