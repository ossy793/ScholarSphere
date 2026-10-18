"""Copies the nightly encrypted database dumps to Cloudflare R2 and back (design section
12). deploy/scripts/backup.sh makes the dump (pg_dump, then gpg with a passphrase that
never leaves the server) and calls these; restore-test.sh downloads the latest one.

    python -m app.backups upload /var/backups/pritis-dues/2026-10-17.dump.gpg
    python -m app.backups list
    python -m app.backups download-latest /tmp/latest.dump.gpg

Backups go to R2_BACKUP_BUCKET (or R2_BUCKET if that isn't set) under "backups/".
Keep them for as long as you need with an R2 lifecycle rule on that prefix.
"""

import argparse
import sys
from pathlib import Path

from app.core.config import get_settings

PREFIX = "backups/"


def _client_and_bucket():
    import boto3

    s = get_settings()
    bucket = s.r2_backup_bucket or s.r2_bucket
    missing = [n for n in ("r2_account_id", "r2_access_key_id", "r2_secret_access_key") if not getattr(s, n)]
    if missing or not bucket:
        sys.exit(f"Backups need {', '.join(m.upper() for m in missing) or ''} and R2_BACKUP_BUCKET (or R2_BUCKET)")
    client = boto3.client(
        "s3", endpoint_url=f"https://{s.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=s.r2_access_key_id, aws_secret_access_key=s.r2_secret_access_key, region_name="auto",
    )
    return client, bucket


def upload(path: Path) -> str:
    if not path.name.endswith(".gpg"):
        sys.exit("Refusing to upload a backup that isn't encrypted (.gpg)")
    client, bucket = _client_and_bucket()
    key = PREFIX + path.name
    client.upload_file(str(path), bucket, key)  # multipart for large files
    head = client.head_object(Bucket=bucket, Key=key)
    if head["ContentLength"] != path.stat().st_size:
        sys.exit(f"Uploaded size {head['ContentLength']} doesn't match {path.stat().st_size}")
    return key


def list_backups() -> list[dict]:
    client, bucket = _client_and_bucket()
    items: list[dict] = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=PREFIX):
        items.extend(page.get("Contents", []))
    return sorted(items, key=lambda o: o["LastModified"])


def download_latest(dest: Path) -> str:
    items = list_backups()
    if not items:
        sys.exit("No backups in R2 yet")
    client, bucket = _client_and_bucket()
    key = items[-1]["Key"]
    client.download_file(bucket, key, str(dest))
    return key


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.backups")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("upload").add_argument("path", type=Path)
    commands.add_parser("list")
    commands.add_parser("download-latest").add_argument("dest", type=Path)
    args = parser.parse_args()
    if args.command == "upload":
        print(f"Uploaded {upload(args.path)}")
    elif args.command == "list":
        for o in list_backups():
            print(f"{o['LastModified']:%Y-%m-%d %H:%M}  {o['Size']:>12,}  {o['Key']}")
    else:
        print(f"Downloaded {download_latest(args.dest)} to {args.dest}")


if __name__ == "__main__":
    main()
