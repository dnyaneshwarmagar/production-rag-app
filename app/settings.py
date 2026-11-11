"""Settings for the production API. Values come from the environment or .env."""
from __future__ import annotations

import os

import rag_core.config  # loads .env

_ = rag_core.config.ROOT

DATABASE_URL = os.getenv("DATABASE_URL", "")
REDIS_URL = os.getenv("REDIS_URL", "")
QUEUE_NAME = os.getenv("QUEUE_NAME", "ingest")

S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_PREFIX = os.getenv("S3_PREFIX", "docs/")
AWS_REGION = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", ""))

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))
ALLOWED_EXTENSIONS = {".pdf", ".md", ".txt", ".html", ".htm", ".csv"}

CHUNK_MAX_TOKENS = int(os.getenv("CHUNK_MAX_TOKENS", "250"))
CANDIDATES = int(os.getenv("CANDIDATES", "40"))
TOP_K = int(os.getenv("TOP_K", "5"))
USE_RERANKER = os.getenv("USE_RERANKER", "1") == "1"
HNSW_EF_SEARCH = int(os.getenv("HNSW_EF_SEARCH", "100"))

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
# Server-side scripts may pass this plus X-User-Groups. Browsers never do.
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")
