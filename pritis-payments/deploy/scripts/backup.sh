#!/usr/bin/env bash
# Nightly backup (design section 12): pg_dump -> gpg (AES-256, passphrase that lives only
# on the server and in your password manager) -> Cloudflare R2. Keeps 7 days on the server.
# Run by dpp-backup.timer as the dpp user, with /etc/pritis-dues/env loaded.
set -euo pipefail

BACKUP_DIR=/srv/pritis-dues/var/backups
PASSPHRASE_FILE=/etc/pritis-dues/backup-passphrase
VENV=/srv/pritis-dues/venv
BACKEND=/srv/pritis-dues/repo/pritis-payments/backend

mkdir -p "$BACKUP_DIR"
umask 077
stamp=$(date -u +%Y-%m-%dT%H%MZ)
out="$BACKUP_DIR/pritis-dues-$stamp.dump.gpg"
# pg_dump wants a plain libpq URL, not SQLAlchemy's postgresql+asyncpg://
db_url="${DATABASE_URL/postgresql+asyncpg:/postgresql:}"

pg_dump --format=custom --no-owner "$db_url" \
  | gpg --batch --yes --symmetric --cipher-algo AES256 --passphrase-file "$PASSPHRASE_FILE" -o "$out"

# Refuse to call it a backup unless it decrypts and pg_restore can read its table of contents.
gpg --batch --quiet --decrypt --passphrase-file "$PASSPHRASE_FILE" "$out" | pg_restore --list >/dev/null

cd "$BACKEND"
"$VENV/bin/python" -m app.backups upload "$out"

find "$BACKUP_DIR" -name 'pritis-dues-*.dump.gpg' -mtime +7 -delete
echo "Backup done: $out ($(du -h "$out" | cut -f1))"
