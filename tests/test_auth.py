"""Password hashing, sessions, and role groups. No database."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("EMBEDDER", "hashing")
os.environ.setdefault("LLM_PROVIDER", "none")
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest

from app.auth import groups_for, hash_password, make_session, session_user_id, signup_role, verify_password  # noqa: E402


def test_password_roundtrip():
    stored = hash_password("correct horse")
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong", stored)


def test_session_roundtrip_and_bad_signature():
    token = make_session("11111111-1111-1111-1111-111111111111", secret="test-secret", ttl=60)
    assert session_user_id(token, secret="test-secret") == "11111111-1111-1111-1111-111111111111"
    assert session_user_id(token + "x", secret="test-secret") is None
    assert session_user_id(token, secret="other") is None


def test_signup_rejects_admin():
    assert signup_role("employee") == "employee"
    assert signup_role("public") == "public"
    assert signup_role("hr") == "hr"
    with pytest.raises(ValueError):
        signup_role("admin")


def test_roles_map_to_document_groups():
    assert groups_for("public") == ["public"]
    assert groups_for("employee") == ["public", "employees"]
    assert groups_for("hr") == ["public", "employees", "hr"]
    assert groups_for("admin", ["hr"]) == ["hr"]
