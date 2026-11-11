"""Pure helpers for importing documents from an S3 prefix. No AWS calls."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("EMBEDDER", "hashing")
os.environ.setdefault("LLM_PROVIDER", "none")

from app.s3_sync import is_importable, normalize_prefix, resolve_doc_meta  # noqa: E402


def test_prefix_always_has_one_trailing_slash():
    assert normalize_prefix("docs") == "docs/"
    assert normalize_prefix("/docs/") == "docs/"
    assert normalize_prefix("") == ""


def test_importable_skips_metadata_and_unknown_types():
    allowed = {".md", ".pdf", ".txt", ".html", ".htm", ".csv"}
    assert is_importable("docs/refund_policy_2025.md", allowed)
    assert not is_importable("docs/_metadata.json", allowed)
    assert not is_importable("docs/.keep", allowed)
    assert not is_importable("docs/notes.docx", allowed)


def test_metadata_sidecar_and_object_tags():
    sidecar = {"salary_bands_confidential.md": {"title": "Salary Bands", "access": ["hr"], "trust": "trusted"}}
    meta = resolve_doc_meta("salary_bands_confidential.md", sidecar, {"access": "hr,employees", "title": "FY2026 bands"})
    assert meta["title"] == "FY2026 bands"
    assert meta["access"] == ["hr", "employees"]
    assert meta["trust"] == "trusted"
    plain = resolve_doc_meta("delivery_and_shipping.md", {}, {})
    assert plain["access"] == ["public"]
    assert plain["title"] == "Delivery And Shipping"
