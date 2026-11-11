"""
The RAG HTTP API.

    uvicorn app.api:app --reload --port 8000
    open http://localhost:8000/docs   (interactive API docs, made by FastAPI)

Endpoints
    POST   /documents        upload a file (+ metadata) -> 202 Accepted, ingestion runs in the background
    POST   /s3/sync          index files already sitting in the S3 bucket (S3_PREFIX)
    GET    /documents        list documents and their status
    GET    /documents/{id}   status of one document: uploaded/queued/processing/indexed/failed
    DELETE /documents/{id}   remove a document and all its chunks
    POST   /query            ask a question -> answer + citations + trace id + timings
    GET    /health           database, queue and model checks

Sign in with POST /login. POST /signup creates a public, employee, or hr account and signs it in.
The session cookie decides which documents you can search.
A browser cannot choose its own groups. Scripts may send X-Internal-Token plus X-User-Groups.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, File, Form, Header, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from redis import Redis
from rq import Queue

from rag_core.documents import Chunk
from rag_core.embeddings import fingerprint, get_embedder
from rag_core.generate import answer
from rag_core.hybrid import Hit
from rag_core.llm import LLM
from rag_core.observability import Tracer, langsmith_enabled
from rag_core.rerank import get_reranker

from . import auth, settings
from .db import connect, hybrid_search, migrate, pgvector_version
from .jobs import enqueue_ingest
from .s3_sync import sync_bucket
from .storage import get_store

app = FastAPI(title="Nimbu Mart RAG API", version="1.0",
              description="Production RAG reference app - Future with Shivank (Shivank Agrawal)")

EMBEDDER = get_embedder()
MODEL_ID = fingerprint(EMBEDDER)
LLM_CLIENT = LLM()
RERANKER = get_reranker() if settings.USE_RERANKER else None
TRACER = Tracer(None)
log = logging.getLogger("uvicorn.error")
QUEUE = Queue(settings.QUEUE_NAME, connection=Redis.from_url(settings.REDIS_URL))
PGVECTOR_VERSION = migrate(EMBEDDER.dim)  # create tables on start-up (use Alembic in a bigger app)


def _groups(header: str | None) -> list[str]:
    groups = [g.strip() for g in (header or "public").split(",") if g.strip()]
    return groups or ["public"]


def _load_user(rag_session: str | None) -> dict | None:
    user_id = auth.session_user_id(rag_session or "")
    if not user_id:
        return None
    with connect() as conn:
        row = conn.execute("SELECT id, username, role, access FROM users WHERE id = %s", (user_id,)).fetchone()
    if not row:
        return None
    return {**row, "id": str(row["id"]), "access": list(row["access"]),
            "groups": auth.groups_for(row["role"], list(row["access"]))}


def current_user(rag_session: str | None = Cookie(default=None)) -> dict:
    user = _load_user(rag_session)
    if not user:
        raise HTTPException(401, "sign in required")
    return user


def require_admin(rag_session: str | None = Cookie(default=None),
                  x_internal_token: str | None = Header(default=None)) -> dict:
    if auth.tokens_match(x_internal_token, settings.INTERNAL_API_TOKEN):
        return {"id": "", "username": "internal", "role": "admin", "access": ["public", "employees", "hr"],
                "groups": ["public", "employees", "hr"]}
    user = _load_user(rag_session)
    if not user:
        raise HTTPException(401, "sign in required")
    if user["role"] != "admin":
        raise HTTPException(403, "admin only")
    return user


def _sees_document(user: dict, access: list[str]) -> bool:
    if user["role"] == "admin":
        return True
    return bool(set(user["groups"]) & set(access or []))


def _enqueue(doc_id: str) -> None:
    enqueue_ingest(doc_id)


@app.post("/documents", status_code=202)
async def upload(file: UploadFile = File(...), title: str = Form(""), department: str = Form("general"),
                 access: str = Form("public"), doc_type: str = Form("document"), version: str = Form("1"),
                 status: str = Form("active"), trust: str = Form("trusted"),
                 user: dict = Depends(require_admin)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(415, f"file type {ext or '?'} not supported; allowed: {sorted(settings.ALLOWED_EXTENSIONS)}")
    data = await file.read()
    if len(data) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"file is larger than {settings.MAX_UPLOAD_MB} MB")
    if not data:
        raise HTTPException(400, "empty file")
    sha = hashlib.sha256(data).hexdigest()
    with connect() as conn:
        existing = conn.execute("SELECT id, status FROM documents WHERE sha256 = %s", (sha,)).fetchone()
        if existing:  # idempotent: the same bytes were uploaded before
            if existing["status"] == "failed":
                _enqueue(str(existing["id"]))
            return {"id": str(existing["id"]), "status": existing["status"], "duplicate": True}
        doc_id = str(uuid.uuid4())
        key = f"documents/{doc_id}/{file.filename}"
        get_store().put(key, data)
        meta = {"title": title or Path(file.filename).stem.replace("_", " ").title(), "department": department,
                "doc_type": doc_type, "version": version, "status": status, "trust": trust}
        conn.execute("INSERT INTO documents (id, filename, sha256, blob_key, status, metadata, access) "
                     "VALUES (%s, %s, %s, %s, 'queued', %s, %s)",
                     (doc_id, file.filename, sha, key, json.dumps(meta), [a.strip() for a in access.split(",")]))
        conn.commit()
    _enqueue(doc_id)
    return {"id": doc_id, "status": "queued", "duplicate": False}


@app.get("/documents")
def list_documents(user: dict = Depends(current_user)):
    with connect() as conn:
        if user["role"] == "admin":
            rows = conn.execute("SELECT d.id, d.filename, d.status, d.access, d.attempts, d.error, "
                                "(SELECT count(*) FROM chunks c WHERE c.document_id = d.id) AS chunks "
                                "FROM documents d ORDER BY d.created_at").fetchall()
        else:
            rows = conn.execute("SELECT d.id, d.filename, d.status, d.access, d.attempts, d.error, "
                                "(SELECT count(*) FROM chunks c WHERE c.document_id = d.id) AS chunks "
                                "FROM documents d WHERE d.access && %s ORDER BY d.created_at",
                                (user["groups"],)).fetchall()
    return [{**r, "id": str(r["id"]), "access": list(r["access"])} for r in rows]


@app.get("/documents/{doc_id}")
def get_document(doc_id: str, user: dict = Depends(current_user)):
    with connect() as conn:
        r = conn.execute("SELECT id, filename, status, attempts, error, metadata, access, updated_at, "
                         "(SELECT count(*) FROM chunks c WHERE c.document_id = d.id) AS chunks "
                         "FROM documents d WHERE id = %s", (doc_id,)).fetchone()
    if not r or not _sees_document(user, list(r["access"])):
        raise HTTPException(404, "document not found")
    return {**r, "id": str(r["id"]), "access": list(r["access"])}


@app.delete("/documents/{doc_id}")
def delete_document(doc_id: str, user: dict = Depends(require_admin)):
    with connect() as conn:
        r = conn.execute("DELETE FROM documents WHERE id = %s RETURNING blob_key", (doc_id,)).fetchone()
        conn.commit()
    if not r:
        raise HTTPException(404, "document not found")
    # Files you dropped under S3_PREFIX stay in the bucket. Only objects this API wrote are removed.
    if str(r["blob_key"]).startswith("documents/"):
        get_store().delete(r["blob_key"])
    return {"deleted": doc_id}


@app.post("/s3/sync", status_code=202)
def import_from_s3(user: dict = Depends(require_admin)):
    """Index every supported file under S3_PREFIX. Unchanged files are left alone."""
    if not settings.S3_BUCKET:
        raise HTTPException(400, "S3_BUCKET is not set")
    try:
        return sync_bucket()
    except Exception as e:
        raise HTTPException(502, f"could not import from s3://{settings.S3_BUCKET}: {e}")


class Question(BaseModel):
    question: str = Field(..., min_length=2, max_length=2000)
    include_archived: bool = False
    top_k: int = Field(settings.TOP_K, ge=1, le=20)


@app.post("/query")
def query(body: Question, rag_session: str | None = Cookie(default=None),
          x_internal_token: str | None = Header(default=None),
          x_user_groups: str | None = Header(default=None)):
    if auth.tokens_match(x_internal_token, settings.INTERNAL_API_TOKEN):
        groups = _groups(x_user_groups)
    else:
        user = _load_user(rag_session)
        if not user:
            raise HTTPException(401, "sign in required")
        groups = user["groups"]
    trace = TRACER.start("query", groups=groups, question=body.question)
    with trace.span("embed_query"):
        qvec = EMBEDDER.embed_query(body.question)
    with trace.span("hybrid_search") as sp, connect() as conn:
        rows = hybrid_search(conn, body.question, qvec, groups, MODEL_ID, limit=settings.CANDIDATES,
                             include_archived=body.include_archived)
        sp["candidates"] = len(rows)
    hits = [Hit(Chunk(r["id"], str(r["document_id"]), r["text"], r["metadata"]), float(r["rrf"]), float(r["cosine"]))
            for r in rows]
    if RERANKER and hits:
        with trace.span("rerank", model=RERANKER.name):
            hits = RERANKER.rerank(body.question, hits, top_n=body.top_k)
    hits = hits[: body.top_k]
    with trace.span("generate") as sp:
        result = answer(body.question, hits, LLM_CLIENT, min_similarity=LLM_CLIENT.s.min_similarity)
        sp.update(mode=result.mode, abstained=result.abstained)
        if result.usage:
            sp.update(input_tokens=result.usage.input_tokens, output_tokens=result.usage.output_tokens)
    trace.set(mode=result.mode, abstained=result.abstained, answer=result.text,
              citations=[c.get("title") or c.get("id") for c in result.citations])
    done = trace.end()
    log.info(json.dumps({"trace_id": done["trace_id"], "total_ms": done["total_ms"],
                         "timings_ms": {s["name"]: s["ms"] for s in done["spans"]}}))
    payload = {"answer": result.text, "abstained": result.abstained, "mode": result.mode,
               "citations": result.citations, "trace_id": done["trace_id"],
               "timings_ms": {s["name"]: s["ms"] for s in done["spans"]}, "total_ms": done["total_ms"]}
    if done.get("langsmith_run_id"):
        payload["langsmith_run_id"] = done["langsmith_run_id"]
    return payload


@app.get("/health")
def health():
    status = {"embedding_model": MODEL_ID, "llm": repr(LLM_CLIENT),
              "reranker": RERANKER.name if RERANKER else None,
              "langsmith": langsmith_enabled(),
              "blob_store": (f"s3://{settings.S3_BUCKET}/{settings.S3_PREFIX}" if settings.S3_BUCKET else "local")}
    try:
        with connect() as conn:
            status["pgvector"] = pgvector_version(conn)
            status["indexed_chunks"] = conn.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"]
    except Exception as e:
        raise HTTPException(503, f"database unavailable: {e}")
    try:
        status["queue_waiting"] = len(QUEUE)
    except Exception as e:
        raise HTTPException(503, f"redis unavailable: {e}")
    return status


class Login(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=200)


class NewUser(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8, max_length=200)
    role: str
    access: list[str] = Field(default_factory=list)


def _public_user(row: dict) -> dict:
    access = list(row["access"])
    return {"id": str(row["id"]), "username": row["username"], "role": row["role"],
            "access": access, "groups": auth.groups_for(row["role"], access)}


def _valid_username(username: str) -> str:
    username = username.strip().lower()
    if len(username) < 3 or not username.replace(".", "").replace("_", "").replace("-", "").isalnum():
        raise HTTPException(400, "username may contain letters, numbers, dots, underscores, and hyphens")
    return username


def _insert_user(username: str, password: str, role: str, access: list[str]) -> str:
    with connect() as conn:
        taken = conn.execute("SELECT id FROM users WHERE username = %s", (username,)).fetchone()
        if taken:
            raise HTTPException(409, "that username is already taken")
        user_id = str(uuid.uuid4())
        conn.execute("INSERT INTO users (id, username, password_hash, role, access) VALUES (%s, %s, %s, %s, %s)",
                     (user_id, username, auth.hash_password(password), role, access))
        conn.commit()
    return user_id


def _set_session(response: Response, user_id: str) -> None:
    response.set_cookie("rag_session", auth.make_session(user_id), httponly=True,
                        samesite="lax", max_age=auth.SESSION_TTL, path="/")


@app.post("/signup", status_code=201)
def signup(body: NewUser, response: Response):
    """Create a non-admin account and sign it in. Admin accounts are created by an existing admin."""
    try:
        role = auth.signup_role(body.role)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    username = _valid_username(body.username)
    access = auth.groups_for(role)
    user_id = _insert_user(username, body.password, role, access)
    _set_session(response, user_id)
    return {"id": user_id, "username": username, "role": role, "access": access, "groups": access}


@app.post("/login")
def login(body: Login, response: Response):
    username = body.username.strip().lower()
    with connect() as conn:
        row = conn.execute("SELECT id, username, role, access, password_hash FROM users WHERE username = %s",
                           (username,)).fetchone()
    if not row or not auth.verify_password(body.password, row["password_hash"]):
        raise HTTPException(401, "incorrect username or password")
    _set_session(response, str(row["id"]))
    return _public_user(row)


@app.post("/logout")
def logout(response: Response):
    response.delete_cookie("rag_session", path="/")
    return {"ok": True}


@app.get("/me")
def me(user: dict = Depends(current_user)):
    return {k: user[k] for k in ("id", "username", "role", "access", "groups")}


@app.get("/users")
def list_users(user: dict = Depends(require_admin)):
    with connect() as conn:
        rows = conn.execute("SELECT id, username, role, access, created_at FROM users ORDER BY username").fetchall()
    return [_public_user(r) for r in rows]


@app.post("/users", status_code=201)
def create_user(body: NewUser, user: dict = Depends(require_admin)):
    username = _valid_username(body.username)
    if body.role not in auth.ROLES:
        raise HTTPException(400, f"role must be one of {', '.join(auth.ROLES)}")
    access = auth.groups_for(body.role, body.access)
    user_id = _insert_user(username, body.password, body.role, access)
    return {"id": user_id, "username": username, "role": body.role, "access": access,
            "groups": auth.groups_for(body.role, access)}


@app.get("/")
def root():
    return FileResponse(Path(__file__).parent / "static" / "index.html")
