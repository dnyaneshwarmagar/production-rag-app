"""Original files live in S3. The database stores only derived chunks and embeddings."""
from __future__ import annotations

from . import settings


class S3BlobStore:
    """AWS S3. Credentials come from the instance role or from the standard AWS environment variables.

    Set the instance metadata hop limit to 2 so containers can see the instance role.
    """

    def __init__(self, bucket: str = settings.S3_BUCKET, region: str = settings.AWS_REGION):
        import boto3

        if not bucket:
            raise RuntimeError("S3_BUCKET is required")
        self.bucket = bucket
        kwargs = {"region_name": region} if region else {}
        self.s3 = boto3.client("s3", **kwargs)

    def put(self, key: str, data: bytes) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get(self, key: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=key)

    def head(self, key: str) -> dict:
        found = self.s3.head_object(Bucket=self.bucket, Key=key)
        return {"etag": str(found.get("ETag", "")).strip('"'), "size": int(found["ContentLength"]),
                "metadata": dict(found.get("Metadata") or {})}

    def list_keys(self, prefix: str = "") -> list[str]:
        paginator = self.s3.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.endswith("/"):
                    keys.append(key)
        return keys


def get_store() -> S3BlobStore:
    return S3BlobStore()
