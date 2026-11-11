"""Enqueue one document for background ingestion."""
from __future__ import annotations

from redis import Redis
from rq import Queue, Retry

from . import settings
from .worker import ingest_document


def enqueue_ingest(doc_id: str) -> None:
    # 3 retries after 10 s, 30 s and 60 s. Needs `rq worker --with-scheduler`.
    queue = Queue(settings.QUEUE_NAME, connection=Redis.from_url(settings.REDIS_URL))
    queue.enqueue(ingest_document, doc_id, job_timeout=600, retry=Retry(max=3, interval=[10, 30, 60]))
