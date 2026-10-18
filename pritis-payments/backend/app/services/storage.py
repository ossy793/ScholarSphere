"""Private file storage: a local folder in development, a Cloudflare R2 bucket in production.

Files are never public. They are read back through API routes that check the caller's role.
"""

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path

from app.core.config import get_settings


class Storage(ABC):
    @abstractmethod
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    @abstractmethod
    async def get(self, key: str) -> bytes: ...


class LocalStorage(Storage):
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError(f"Bad storage key {key!r}")
        return path

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)


class R2Storage(Storage):
    """Cloudflare R2 through its S3-compatible API. boto3 is synchronous, so calls run in a thread."""

    def __init__(self, account_id: str, access_key_id: str, secret_access_key: str, bucket: str) -> None:
        import boto3  # only needed when R2 is configured

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
        )

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self.client.put_object, Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
        )

    async def get(self, key: str) -> bytes:
        obj = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key)
        return await asyncio.to_thread(obj["Body"].read)


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        s = get_settings()
        if s.storage_backend == "r2":
            missing = [n for n in ("r2_account_id", "r2_access_key_id", "r2_secret_access_key", "r2_bucket")
                       if not getattr(s, n)]
            if missing:
                raise RuntimeError(f"STORAGE_BACKEND=r2 needs {', '.join(m.upper() for m in missing)}")
            _storage = R2Storage(s.r2_account_id, s.r2_access_key_id, s.r2_secret_access_key, s.r2_bucket)
        else:
            _storage = LocalStorage(s.storage_local_dir)
    return _storage
