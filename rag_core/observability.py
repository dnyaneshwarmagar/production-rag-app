"""
Chapter 12 - Tracing, latency and cost.

Every request gets a trace id. Every stage (embed, search, rerank, generate) is a span with its
duration and useful attributes (how many chunks, scores, token counts). Traces are written as
JSON lines to logs/traces.jsonl when a sink path is set.

When LANGSMITH_API_KEY is set, the same trace is also sent to LangSmith: one root run per query
or ingest job, and one child run per span. A LangSmith outage is logged once and does not fail
the request.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from . import config

_ = config.ROOT
log = logging.getLogger("rag.langsmith")
_warned = False
_INPUT_KEYS = {"question", "document_id", "groups"}


def langsmith_enabled() -> bool:
    """True when a key is set and LANGSMITH_TRACING is not explicitly turned off."""
    if not os.getenv("LANGSMITH_API_KEY", "").strip():
        return False
    flag = os.getenv("LANGSMITH_TRACING", "true").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def _run_type(name: str) -> str:
    if name in {"embed", "embed_query"}:
        return "embedding"
    if name in {"hybrid_search", "retrieve", "rerank"}:
        return "retriever"
    if name == "generate":
        return "llm"
    if name in {"load", "extract"}:
        return "parser"
    return "chain"


def _jsonable(value, limit: int = 4000):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, dict):
        return {str(k): _jsonable(v, limit) for k, v in value.items() if k not in {"embedding", "qvec", "vector"}}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v, limit) for v in list(value)[:50]]
    return str(value)[:limit]


def _warn(exc: Exception) -> None:
    global _warned
    if not _warned:
        _warned = True
        log.warning("LangSmith tracing failed: %s", exc)


def _start_run(name: str, run_type: str, inputs: dict, extra: dict, parent=None):
    from langsmith.run_trees import RunTree

    if parent is not None:
        return parent.create_child(name=name, run_type=run_type, inputs=inputs, extra=extra)
    return RunTree(
        name=name, run_type=run_type, inputs=inputs, extra=extra,
        project_name=os.getenv("LANGSMITH_PROJECT") or "nimbu-mart", tags=["nimbu-mart"],
    )


def _post(run) -> None:
    try:
        run.post()
    except Exception as exc:
        _warn(exc)


def _finish(run, *, outputs: dict | None = None, error: str | None = None) -> None:
    try:
        run.end(outputs=outputs or {}, error=error)
        run.patch()
    except Exception as exc:
        _warn(exc)


class Trace:
    def __init__(self, name: str, sink: Path | None, **attrs):
        self.id = uuid.uuid4().hex[:12]
        self.name = name
        self.attrs = dict(attrs)
        self.spans: list[dict] = []
        self._t0 = time.perf_counter()
        self._sink = sink
        self._ended = False
        self._run = None
        self.langsmith_run_id: str | None = None
        if langsmith_enabled():
            inputs = {k: _jsonable(v) for k, v in attrs.items() if k in _INPUT_KEYS}
            extra = {"metadata": {"trace_id": self.id, **{k: _jsonable(v) for k, v in attrs.items() if k not in _INPUT_KEYS}}}
            try:
                self._run = _start_run(name, "chain", inputs, extra)
                self.langsmith_run_id = str(self._run.id)
                _post(self._run)
            except Exception as exc:
                self._run = None
                _warn(exc)

    @contextmanager
    def span(self, name: str, **attrs):
        t0 = time.perf_counter()
        record = {"name": name, **attrs}
        child = None
        if self._run is not None:
            try:
                child = _start_run(name, _run_type(name), _jsonable(attrs), {"metadata": {"trace_id": self.id}}, parent=self._run)
                _post(child)
            except Exception as exc:
                child = None
                _warn(exc)
        try:
            yield record  # the caller may add attributes: record["n_hits"] = 5
        except Exception as exc:
            record["error"] = f"{exc.__class__.__name__}: {exc}"
            raise
        finally:
            record["ms"] = round((time.perf_counter() - t0) * 1000, 2)
            self.spans.append(record)
            if child is not None:
                outputs = {k: _jsonable(v) for k, v in record.items() if k not in attrs and k not in {"name", "error"}}
                _finish(child, outputs=outputs, error=record.get("error"))

    def set(self, **attrs) -> None:
        self.attrs.update(attrs)

    def end(self) -> dict:
        data = {"trace_id": self.id, "name": self.name, "ts": time.time(),
                "total_ms": round((time.perf_counter() - self._t0) * 1000, 2),
                "attrs": self.attrs, "spans": self.spans}
        if self.langsmith_run_id:
            data["langsmith_run_id"] = self.langsmith_run_id
        if self._sink:
            self._sink.parent.mkdir(parents=True, exist_ok=True)
            with self._sink.open("a", encoding="utf-8") as f:
                f.write(json.dumps(data, default=str) + "\n")
        if self._run is not None and not self._ended:
            self._ended = True
            outputs = {k: _jsonable(v) for k, v in self.attrs.items() if k not in _INPUT_KEYS and k != "error"}
            outputs["total_ms"] = data["total_ms"]
            error = self.attrs.get("error")
            if any(s.get("error") for s in self.spans) and not error:
                error = next(s["error"] for s in self.spans if s.get("error"))
            _finish(self._run, outputs=outputs, error=str(error) if error else None)
        return data


class Tracer:
    def __init__(self, sink: Path | None = None):
        self.sink = sink

    def start(self, name: str, **attrs) -> Trace:
        return Trace(name, self.sink, **attrs)


def percentiles(values: list[float], ps=(50, 95, 99)) -> dict[str, float]:
    if not values:
        return {}
    return {f"p{p}": round(float(np.percentile(values, p)), 1) for p in ps}


def estimate_cost(input_tokens: int, output_tokens: int, usd_per_m_in: float, usd_per_m_out: float) -> float:
    """Cost of one LLM call in USD. Prices are per 1 million tokens (check your provider's page)."""
    return input_tokens / 1e6 * usd_per_m_in + output_tokens / 1e6 * usd_per_m_out
