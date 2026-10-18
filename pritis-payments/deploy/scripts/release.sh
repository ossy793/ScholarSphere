#!/usr/bin/env bash
# Release (design section 12): pull the code, install requirements, migrate the database,
# restart both services, check /api/health. Run as root (or with sudo) on the server:
#   sudo /srv/pritis-dues/repo/pritis-payments/deploy/scripts/release.sh [branch]
# Touches only Pritis Class Dues: its own checkout, venv, database and dpp-* services.
set -euo pipefail

BRANCH=${1:-main}
REPO=/srv/pritis-dues/repo
VENV=/srv/pritis-dues/venv
BACKEND=$REPO/pritis-payments/backend
as_dpp() { sudo -u dpp -H "$@"; }

echo "==> Pulling $BRANCH"
as_dpp git -C "$REPO" fetch --quiet origin "$BRANCH"
as_dpp git -C "$REPO" checkout --quiet "$BRANCH"
as_dpp git -C "$REPO" merge --quiet --ff-only "origin/$BRANCH"
echo "    now at $(as_dpp git -C "$REPO" log -1 --format='%h %s')"

echo "==> Installing requirements"
as_dpp "$VENV/bin/pip" install --quiet --disable-pip-version-check -r "$BACKEND/requirements.txt"

echo "==> Backing up before migrating"
systemctl start dpp-backup.service || { echo "Backup failed; not releasing." >&2; exit 1; }

echo "==> Migrating the database"
# set -a exports everything the env file defines (values with spaces must be in quotes).
as_dpp bash -c "set -a; . /etc/pritis-dues/env; set +a; cd '$BACKEND' && '$VENV/bin/alembic' upgrade head"

echo "==> Restarting"
systemctl restart dpp-api.service dpp-worker.service

echo "==> Checking health"
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8100/api/health >/dev/null 2>&1; then
    echo "    healthy"
    systemctl --no-pager --lines=0 status dpp-api.service dpp-worker.service | grep -E 'Active:'
    echo "Release done."
    exit 0
  fi
  sleep 1
done
echo "API not healthy after 30s. Logs: journalctl -u dpp-api -n 100" >&2
exit 1
