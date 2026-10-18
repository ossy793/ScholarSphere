#!/usr/bin/env bash
# Monthly restore test (design section 12): downloads the newest backup from R2, decrypts it,
# restores it into a scratch database and checks the money records make sense. A backup
# nobody has restored is only a hope. Run by dpp-restore-test.timer; by hand:
#   sudo systemctl start dpp-restore-test.service && journalctl -u dpp-restore-test -n 20
# The database role needs CREATEDB (deploy/README.md sets that up).
set -euo pipefail

PASSPHRASE_FILE=/etc/pritis-dues/backup-passphrase
VENV=/srv/pritis-dues/venv
BACKEND=/srv/pritis-dues/repo/pritis-payments/backend
SCRATCH_DB=pritis_dues_restore_test

base_url="${DATABASE_URL/postgresql+asyncpg:/postgresql:}"
scratch_url="${base_url%/*}/$SCRATCH_DB"
work=$(mktemp -d)
trap 'rm -rf "$work"; dropdb --if-exists --maintenance-db="$base_url" "$SCRATCH_DB" || true' EXIT

cd "$BACKEND"
"$VENV/bin/python" -m app.backups download-latest "$work/latest.dump.gpg"
gpg --batch --quiet --decrypt --passphrase-file "$PASSPHRASE_FILE" -o "$work/latest.dump" "$work/latest.dump.gpg"

dropdb --if-exists --maintenance-db="$base_url" "$SCRATCH_DB"
createdb --maintenance-db="$base_url" "$SCRATCH_DB"
pg_restore --no-owner --exit-on-error -d "$scratch_url" "$work/latest.dump"

q() { psql "$scratch_url" -XAtc "$1"; }
version=$(q "SELECT version_num FROM alembic_version")
payments=$(q "SELECT count(*) FROM payments WHERE status IN ('success', 'refunded')")
entries=$(q "SELECT count(*) FROM ledger_entries")
unbalanced=$(q "SELECT count(*) FROM (SELECT txn_id FROM ledger_entries GROUP BY txn_id
  HAVING SUM(CASE WHEN direction = 'debit' THEN amount_kobo ELSE -amount_kobo END) <> 0) t")

echo "Restored: schema $version, $payments paid payments, $entries ledger entries, $unbalanced unbalanced transactions"
if [ "$unbalanced" != "0" ]; then
  echo "RESTORE TEST FAILED: the restored ledger doesn't balance" >&2
  exit 1
fi
echo "RESTORE TEST PASSED"
