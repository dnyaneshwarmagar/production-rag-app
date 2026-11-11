"""Passwords, sessions, and the groups each role may search."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
import uuid

from . import settings

ROLES = ("public", "employee", "hr", "admin")
# Open sign-up cannot create an admin. An existing admin creates those from the Users tab.
SIGNUP_ROLES = ("public", "employee", "hr")
ROLE_GROUPS = {
    "public": ["public"],
    "employee": ["public", "employees"],
    "hr": ["public", "employees", "hr"],
}
SESSION_TTL = 12 * 60 * 60


def signup_role(role: str) -> str:
    """Roles a visitor may choose. Admin is refused."""
    if role not in SIGNUP_ROLES:
        raise ValueError("sign up is only available for public, employee, and hr")
    return role


def groups_for(role: str, access: list[str] | None = None) -> list[str]:
    if role in ROLE_GROUPS:
        return list(ROLE_GROUPS[role])
    if role == "admin":
        groups = [g.strip() for g in (access or []) if g and g.strip()]
        return groups or ["public"]
    raise ValueError(f"unknown role {role}")


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"pbkdf2_sha256$200000${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _secret(secret: str | None) -> bytes:
    value = settings.SESSION_SECRET if secret is None else secret
    if not value:
        raise RuntimeError("SESSION_SECRET is not set")
    return value.encode()


def make_session(user_id: str, secret: str | None = None, ttl: int = SESSION_TTL) -> str:
    payload = f"{user_id}|{int(time.time()) + ttl}"
    sig = hmac.new(_secret(secret), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def session_user_id(token: str, secret: str | None = None) -> str | None:
    try:
        user_id, exp, sig = token.split("|")
        payload = f"{user_id}|{exp}"
        expected = hmac.new(_secret(secret), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if int(exp) < time.time():
            return None
        uuid.UUID(user_id)
        return user_id
    except (ValueError, TypeError):
        return None


def tokens_match(got: str | None, expected: str) -> bool:
    if not expected or not got:
        return False
    return hmac.compare_digest(got.encode(), expected.encode())


def ensure_admin(conn) -> None:
    """Create the bootstrap admin the first time the API starts. Later password changes in the UI stay."""
    username = settings.ADMIN_USERNAME.strip().lower()
    password = settings.ADMIN_PASSWORD
    if not username or not password:
        return
    existing = conn.execute("SELECT id FROM users WHERE username = %s", (username,)).fetchone()
    if existing:
        return
    conn.execute(
        "INSERT INTO users (id, username, password_hash, role, access) VALUES (%s, %s, %s, 'admin', %s)",
        (str(uuid.uuid4()), username, hash_password(password), ["public", "employees", "hr"]),
    )
