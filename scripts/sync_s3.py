"""
Register files already in S3 and enqueue ingestion. Needs DATABASE_URL, REDIS_URL, and S3_BUCKET.

    python scripts/sync_s3.py

From another machine, call the API instead:  curl -X POST http://<host>:8000/s3/sync
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.s3_sync import sync_bucket

print(json.dumps(sync_bucket(), indent=2))
