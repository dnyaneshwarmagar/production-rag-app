"""LangSmith tracing stays off without a key, and mirrors spans when it is on. No network."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("EMBEDDER", "hashing")
os.environ.setdefault("LLM_PROVIDER", "none")

from rag_core.observability import Tracer, langsmith_enabled  # noqa: E402


class _Run:
    def __init__(self, name, run_type, inputs, extra, parent=None):
        self.name = name
        self.run_type = run_type
        self.inputs = inputs
        self.extra = extra
        self.parent = parent
        self.id = "run-" + name
        self.posted = 0
        self.patched = 0
        self.ended = None

    def create_child(self, name, run_type="chain", inputs=None, extra=None):
        return _Run(name, run_type, inputs, extra, parent=self)

    def post(self):
        self.posted += 1

    def end(self, **kwargs):
        self.ended = kwargs

    def patch(self):
        self.patched += 1


def _install(monkeypatch):
    runs = []

    def start(name, run_type, inputs, extra, parent=None):
        if parent is not None:
            run = parent.create_child(name=name, run_type=run_type, inputs=inputs, extra=extra)
        else:
            run = _Run(name, run_type, inputs, extra)
        runs.append(run)
        return run

    monkeypatch.setattr("rag_core.observability._start_run", start)
    return runs


def test_tracing_is_off_without_a_key(monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert langsmith_enabled() is False
    runs = _install(monkeypatch)
    trace = Tracer().start("query", question="hello")
    with trace.span("embed_query"):
        pass
    done = trace.end()
    assert runs == []
    assert "langsmith_run_id" not in done
    assert done["spans"][0]["name"] == "embed_query"
    assert "ms" in done["spans"][0]


def test_tracing_flag_can_disable_a_key(monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-test")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    assert langsmith_enabled() is False


def test_spans_are_posted_to_langsmith(monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-test")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    runs = _install(monkeypatch)
    trace = Tracer().start("query", question="return window?", groups=["public"])
    with trace.span("embed_query"):
        pass
    with trace.span("hybrid_search") as span:
        span["candidates"] = 3
    trace.set(answer="7 days", mode="extractive", abstained=False)
    done = trace.end()
    root, embed, search = runs
    assert done["langsmith_run_id"] == "run-query"
    assert root.inputs["question"] == "return window?"
    assert root.extra["metadata"]["trace_id"] == done["trace_id"]
    assert embed.run_type == "embedding"
    assert search.run_type == "retriever"
    assert search.ended["outputs"]["candidates"] == 3
    assert root.ended["outputs"]["answer"] == "7 days"
    assert root.ended["error"] is None
    assert root.posted == 1 and root.patched == 1


def test_a_failed_span_is_recorded_and_does_not_hide_the_error(monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-test")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    runs = _install(monkeypatch)
    trace = Tracer().start("ingest", document_id="doc-1")
    try:
        with trace.span("embed", model="wordllama"):
            raise RuntimeError("boom")
    except RuntimeError as exc:
        assert str(exc) == "boom"
    done = trace.end()
    assert runs[1].ended["error"] == "RuntimeError: boom"
    assert runs[0].ended["error"] == "RuntimeError: boom"
    assert done["spans"][0]["error"] == "RuntimeError: boom"
