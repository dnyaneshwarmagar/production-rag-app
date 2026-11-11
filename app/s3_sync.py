"""
Import documents that were dropped into the S3 bucket.

Layout (S3_PREFIX defaults to docs/):

    s3://<bucket>/docs/refund_policy_2025.md
    s3://<bucket>/docs/_metadata.json          optional, same shape as data/raw/_metadata.json

Object user-metadata (access, title, department, status, trust, version, doc_type)
overrides the sidecar file for that one object. Files stay where you put them.
The worker reads those keys; it does not copy them to another folder.

POST /s3/sync   or   python scripts/sync_s3.py
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from . import settings
from .db import connect
from .jobs import enqueue_ingest
from .storage import S3BlobStore, get_store


def normalize_prefix(prefix: str) -> str:
    prefix = (prefix or "").strip().lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return prefix


def is_importable(key: str, allowed: set[str]) -> bool:
    name = key.rstrip("/").rsplit("/", 1)[-1]
    if not name or name.startswith(("_", ".")):
        return False
    return Path(name).suffix.lower() in allowed


def resolve_doc_meta(filename: str, sidecar: dict | None = None, object_meta: dict | None = None) -> dict:
    """Merge defaults, _metadata.json, then S3 object metadata. Access is a list of groups."""
    from_file = (sidecar or {}).get(filename) or {}
    if not isinstance(from_file, dict):
        from_file = {}
    access = from_file.get("access") or ["public"]
    if isinstance(access, str):
        access = [part.strip() for part in access.split(",") if part.strip()]
    meta = {
        "title": from_file.get("title") or Path(filename).stem.replace("_", " ").title(),
        "department": from_file.get("department", "general"),
        "doc_type": from_file.get("doc_type", "document"),
        "version": str(from_file.get("version", "1")),
        "status": from_file.get("status", "active"),
        "trust": from_file.get("trust", "trusted"),
        "access": list(access) or ["public"],
    }
    object_meta = {str(k).lower(): v for k, v in (object_meta or {}).items() if v not in (None, "")}
    for field in ("title", "department", "version", "status", "trust"):
        if object_meta.get(field):
            meta[field] = str(object_meta[field])
    if object_meta.get("doc_type") or object_meta.get("doctype"):
        meta["doc_type"] = str(object_meta.get("doc_type") or object_meta.get("doctype"))
    if object_meta.get("access"):
        groups = [part.strip() for part in str(object_meta["access"]).split(",") if part.strip()]
        meta["access"] = groups or ["public"]
    return meta


def _load_sidecar(store: S3BlobStore, prefix: str) -> dict:
    try:
        payload = json.loads(store.get(prefix + "_metadata.json").decode("utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {name: spec for name, spec in payload.items() if not str(name).startswith("_") and isinstance(spec, dict)}


def _register(conn, store: S3BlobStore, key: str, filename: str, etag: str, meta: dict) -> tuple[str, str]:
    stored = dict(meta)
    access = stored.pop("access")
    stored["s3_etag"] = etag
    stored["s3_key"] = key
    row = conn.execute("SELECT id, status, metadata FROM documents WHERE blob_key = %s", (key,)).fetchone()
    if row and (row["metadata"] or {}).get("s3_etag") == etag and row["status"] != "failed":
        return "unchanged", str(row["id"])

    data = store.get(key)
    if not data:
        return "empty", ""
    sha = hashlib.sha256(data).hexdigest()
    if row:
        clash = conn.execute("SELECT id FROM documents WHERE sha256 = %s AND id <> %s", (sha, row["id"])).fetchone()
        if clash:
            return "duplicate", str(clash["id"])
        conn.execute("UPDATE documents SET sha256 = %s, filename = %s, status = 'queued', metadata = %s, "
                     "access = %s, error = NULL, updated_at = now() WHERE id = %s",
                     (sha, filename, json.dumps(stored), access, row["id"]))
        conn.commit()
        enqueue_ingest(str(row["id"]))
        return "updated", str(row["id"])

    existing = conn.execute("SELECT id, status FROM documents WHERE sha256 = %s", (sha,)).fetchone()
    if existing:
        if existing["status"] == "failed":
            enqueue_ingest(str(existing["id"]))
            return "requeued", str(existing["id"])
        return "duplicate", str(existing["id"])

    doc_id = str(uuid.uuid4())
    conn.execute("INSERT INTO documents (id, filename, sha256, blob_key, status, metadata, access) "
                 "VALUES (%s, %s, %s, %s, 'queued', %s, %s)",
                 (doc_id, filename, sha, key, json.dumps(stored), access))
    conn.commit()
    enqueue_ingest(doc_id)
    return "imported", doc_id


def sync_bucket() -> dict:
    """List S3_PREFIX, register new or changed files, and enqueue ingestion."""
    if not settings.S3_BUCKET:
        raise RuntimeError("S3_BUCKET is not set")
    store = get_store()
    if not isinstance(store, S3BlobStore):
        raise RuntimeError("S3_BUCKET is not set")
    prefix = normalize_prefix(settings.S3_PREFIX)
    keys = store.list_keys(prefix)
    sidecar = _load_sidecar(store, prefix)
    report: dict[str, list] = {"imported": [], "updated": [], "requeued": [], "unchanged": [], "skipped": [], "duplicate": [], "empty": []}
    limit = settings.MAX_UPLOAD_MB * 1024 * 1024
    with connect() as conn:
        for key in keys:
            if not is_importable(key, settings.ALLOWED_EXTENSIONS):
                continue
            filename = key.rstrip("/").rsplit("/", 1)[-1]
            head = store.head(key)
            if head["size"] > limit:
                report["skipped"].append({"key": key, "reason": f"larger than {settings.MAX_UPLOAD_MB} MB"})
                continue
            meta = resolve_doc_meta(filename, sidecar, head["metadata"])
            action, doc_id = _register(conn, store, key, filename, head["etag"], meta)
            report[action].append({"key": key, "id": doc_id} if doc_id else {"key": key})
    return {"bucket": settings.S3_BUCKET, "prefix": prefix,
            "counts": {name: len(items) for name, items in report.items()}, **report}
