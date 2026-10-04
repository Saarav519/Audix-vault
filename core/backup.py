"""Database and bucket backups to a separate S3-compatible bucket."""

from __future__ import annotations

import gzip
import os
import subprocess

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

DB_PREFIX = "db/"
FILES_PREFIX = "files/"


def backup_client():
    import boto3
    from botocore.config import Config

    if not settings.BACKUP_S3_BUCKET_NAME:
        raise ImproperlyConfigured("BACKUP_S3_BUCKET_NAME is not set.")
    return boto3.client(
        "s3", endpoint_url=settings.BACKUP_S3_ENDPOINT_URL or None,
        aws_access_key_id=settings.BACKUP_S3_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.BACKUP_S3_SECRET_ACCESS_KEY or None,
        region_name=settings.BACKUP_S3_REGION or None, config=Config(signature_version="s3v4"),
    )


def _pg_env():
    db = settings.DATABASES["default"]
    env = dict(os.environ)
    env.update({"PGHOST": str(db.get("HOST") or "localhost"), "PGPORT": str(db.get("PORT") or 5432),
                "PGUSER": str(db.get("USER") or ""), "PGPASSWORD": str(db.get("PASSWORD") or ""),
                "PGDATABASE": str(db.get("NAME") or "")})
    return env


def dump_database() -> bytes:
    """pg_dump in plain SQL, gzip-compressed."""
    out = subprocess.run(["pg_dump", "--no-owner", "--no-privileges", "--clean", "--if-exists"],
                         env=_pg_env(), capture_output=True, check=True, timeout=1800)
    return gzip.compress(out.stdout)


def restore_sql(gz_bytes: bytes):
    sql = gzip.decompress(gz_bytes)
    subprocess.run(["psql", "--set", "ON_ERROR_STOP=1", "--quiet"], input=sql, env=_pg_env(),
                   capture_output=True, check=True, timeout=3600)


def latest_db_key(client, bucket) -> str | None:
    keys = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=DB_PREFIX):
        keys += [o["Key"] for o in page.get("Contents", [])]
    return max(keys) if keys else None


def sync_bucket(src_client, src_bucket, dst_client, dst_bucket) -> tuple[int, int]:
    """Copy new or changed objects from the files bucket into the backup bucket. Returns (copied, skipped)."""
    existing = {}
    for page in dst_client.get_paginator("list_objects_v2").paginate(Bucket=dst_bucket, Prefix=FILES_PREFIX):
        for o in page.get("Contents", []):
            existing[o["Key"][len(FILES_PREFIX):]] = o["Size"]
    copied = skipped = 0
    for page in src_client.get_paginator("list_objects_v2").paginate(Bucket=src_bucket):
        for o in page.get("Contents", []):
            if existing.get(o["Key"]) == o["Size"]:
                skipped += 1
                continue
            body = src_client.get_object(Bucket=src_bucket, Key=o["Key"])
            dst_client.upload_fileobj(body["Body"], dst_bucket, FILES_PREFIX + o["Key"],
                                      ExtraArgs={"ContentType": body.get("ContentType", "application/octet-stream")})
            copied += 1
    return copied, skipped
