"""Private file storage: S3-compatible (production) or Local (development and tests).

Files are never public. Uploads use a presigned POST (S3) or a signed one-time
endpoint (Local). Downloads are short-lived presigned / signed URLs that are only
issued after a permission check in the calling view.
"""

from __future__ import annotations

import mimetypes
import re
import unicodedata
import uuid
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.core import signing
from django.urls import reverse

UPLOAD_SALT = "audix.upload"
DOWNLOAD_SALT = "audix.download"


class StorageNotConfigured(Exception):
    pass


def safe_filename(name: str, default: str = "file") -> str:
    name = Path(name or "").name
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-._")
    if not name:
        name = default
    stem, dot, ext = name.rpartition(".")
    if dot:
        name = f"{stem[:80]}.{ext[:10].lower()}"
    return name[:100]


def extension(name: str) -> str:
    return Path(name or "").suffix.lower()


def build_key(client_id, audit_id, kind: str, filename: str) -> str:
    return f"clients/{client_id}/audits/{audit_id}/{kind}/{uuid.uuid4().hex}-{safe_filename(filename)}"


def content_disposition(filename: str, inline: bool) -> str:
    kind = "inline" if inline else "attachment"
    ascii_name = safe_filename(filename)
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename or ascii_name)}"


def guess_type(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


class S3Backend:
    name = "s3"

    def __init__(self, endpoint, bucket, key_id, secret, region, addressing_style="path"):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint or None,
            aws_access_key_id=key_id or None,
            aws_secret_access_key=secret or None,
            region_name=region or None,
            config=Config(signature_version="s3v4", s3={"addressing_style": addressing_style},
                          retries={"max_attempts": 3}),
        )

    def presign_upload(self, key, content_type, max_bytes, minutes):
        post = self.client.generate_presigned_post(
            Bucket=self.bucket, Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, int(max_bytes)]],
            ExpiresIn=int(minutes * 60),
        )
        return {"method": "POST", "url": post["url"], "fields": post["fields"], "file_field": "file"}

    def head(self, key):
        from botocore.exceptions import ClientError

        try:
            r = self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError:
            return None
        return {"size": r["ContentLength"], "content_type": r.get("ContentType", "")}

    def download_url(self, key, filename, inline, content_type, minutes):
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key,
                    "ResponseContentDisposition": content_disposition(filename, inline),
                    "ResponseContentType": content_type or guess_type(filename)},
            ExpiresIn=int(minutes * 60),
        )

    def put(self, key, data: bytes, content_type):
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get(self, key) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def stream(self, key, chunk=1024 * 256):
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        yield from body.iter_chunks(chunk)

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=key)


class LocalBackend:
    """Development and tests only. Files live under PRIVATE_MEDIA_ROOT and are served by
    an authenticated Django view through signed, expiring links."""

    name = "local"

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key) -> Path:
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError("Invalid key")
        return p

    def presign_upload(self, key, content_type, max_bytes, minutes):
        token = signing.dumps({"k": key, "ct": content_type, "max": int(max_bytes)}, salt=UPLOAD_SALT)
        return {"method": "POST", "url": reverse("files:local_upload", args=[token]), "fields": {},
                "file_field": "file"}

    def head(self, key):
        p = self._path(key)
        if not p.exists():
            return None
        meta = p.with_name(p.name + ".type")
        ct = meta.read_text() if meta.exists() else guess_type(p.name)
        return {"size": p.stat().st_size, "content_type": ct}

    def download_url(self, key, filename, inline, content_type, minutes):
        token = signing.dumps({"k": key, "fn": filename, "in": bool(inline), "ct": content_type},
                              salt=DOWNLOAD_SALT)
        return reverse("files:local_serve", args=[token])

    def put(self, key, data: bytes, content_type):
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        p.with_name(p.name + ".type").write_text(content_type or "")

    def get(self, key) -> bytes:
        return self._path(key).read_bytes()

    def stream(self, key, chunk=1024 * 256):
        with open(self._path(key), "rb") as fh:
            while True:
                data = fh.read(chunk)
                if not data:
                    break
                yield data

    def delete(self, key):
        p = self._path(key)
        p.unlink(missing_ok=True)
        p.with_name(p.name + ".type").unlink(missing_ok=True)


@lru_cache(maxsize=4)
def _s3(endpoint, bucket, key_id, secret, region, style):
    return S3Backend(endpoint, bucket, key_id, secret, region, style)


def get_backend():
    """The active backend, or None when file storage is not configured."""
    if settings.S3_BUCKET_NAME:
        return _s3(settings.S3_ENDPOINT_URL, settings.S3_BUCKET_NAME, settings.S3_ACCESS_KEY_ID,
                   settings.S3_SECRET_ACCESS_KEY, settings.S3_REGION, settings.S3_ADDRESSING_STYLE)
    if settings.ALLOW_LOCAL_STORAGE:
        return LocalBackend(settings.PRIVATE_MEDIA_ROOT)
    return None


def require_backend():
    backend = get_backend()
    if backend is None:
        raise StorageNotConfigured("File storage is not configured yet")
    return backend


def is_configured() -> bool:
    return get_backend() is not None


def signed_minutes() -> int:
    from core.models import AppSettings

    return AppSettings.load().signed_url_minutes or settings.SIGNED_URL_MINUTES
