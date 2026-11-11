# How the application works

Nimbu Mart is a fictional company used as the document corpus. The application is a retrieval-augmented question answering service: people sign in, ask a question, and get an answer that quotes the company documents they are allowed to see.

A new developer can treat the repository as two layers.

| Layer | Path | Role |
|---|---|---|
| Library | `rag_core/` | Load, clean, chunk, embed, search, and write a grounded answer. Used by the API and by the unit tests. |
| Service | `app/` | FastAPI, the background worker, Postgres, S3, sessions, and the browser page. |

The browser is one page, `app/static/index.html`, served by the API at `GET /`. There is no separate frontend process.

## Project flow

```text
                         Browser page
                              |
                              v
                           FastAPI
                              |
                              v
         Sign up or sign in (cookie rag_session)
                              |
          +-------------------+-------------------+
          |                   |                   |
          v                   v                   v
   Ask: POST /query     Documents the       Admin only:
                        role can see        upload, S3 sync,
                                            delete, users
          |                                       |
          |                    +------------------+
          |                    |                  |
          |                    v                  v
          |             Postgres row        S3 original file
          |             status queued              |
          |                    |                   |
          |                    v                   |
          |             Redis queue ingest         |
          |                    |                   |
          |                    v                   |
          |      Worker: load, redact, chunk, embed
          |                    |<------------------+
          |                    v
          |      Postgres chunks and embeddings
          |             status indexed
          |                    |
          +--------------------+
                               |
                               v
              Embed the question, hybrid search
        group overlap, no injection, matching model
                               |
                               v
           Grounded answer with citations, or abstain
```

Sign-up can create `public`, `employee`, or `hr`. An admin account is created from the Users area, or once at startup from `ADMIN_USERNAME` when that username is missing. A visitor who is not an admin never reaches the upload, sync, delete, or user steps.

## What a user can do

The page at `http://<server>:8000/` has four areas. Which ones appear depends on the signed-in role.

| Area | Who sees it | What it does |
|---|---|---|
| Ask | Everyone who is signed in | Sends the question to `POST /query`. Shows the answer, whether the system abstained, the time, the trace id, and the citations. |
| Documents | Everyone who is signed in | Lists documents the caller is allowed to see. Rows that are still `queued` or `processing` refresh every 3 seconds. Clicking a row loads `GET /documents/{id}`. |
| Add | Admin | Uploads a file with `POST /documents`, or indexes files already in S3 with `POST /s3/sync`. |
| Users | Admin | Creates accounts with `POST /users`, including admin accounts. |

Sign-up is on the same page, behind **Create an account**. A visitor can create a `public`, `employee`, or `hr` account and is signed in immediately. Choosing `admin` is rejected. An existing admin creates further admin accounts from the Users tab.

There is no public password-reset flow. The first admin is inserted at startup from `ADMIN_USERNAME` and `ADMIN_PASSWORD` when that username does not already exist. Later password changes in the database are left alone.

## Roles and document groups

Every document has an `access` list, such as `["public"]` or `["hr"]`. A user searches the groups attached to their role. A document is visible when those two lists overlap.

| Role | Groups searched | Typical documents |
|---|---|---|
| `public` | `public` | Refund policy, shipping, catalog, membership, warranty, FAQ |
| `employee` | `public`, `employees` | Also the error-code runbook, leave policy, support tickets, travel policy, vendor guide |
| `hr` | `public`, `employees`, `hr` | Also the confidential salary bands |
| `admin` | The groups stored on that user | The bootstrap admin is stored with `public`, `employees`, and `hr`, so they can search everything |

Admin is also the only role that can upload, sync from S3, delete a document, and manage users. A non-admin who requests a document id outside their groups receives 404.

The browser never sends a groups header. `POST /query` reads the session cookie and uses that user's groups. A script may pass `X-User-Groups` only when it also sends a matching `X-Internal-Token`. That token is `INTERNAL_API_TOKEN` in `.env`. `scripts/ask.py` does this.

## Sign-in

`POST /login` checks the username and a PBKDF2-SHA256 password hash (200,000 rounds) and sets an `HttpOnly` cookie named `rag_session`. The cookie lasts 12 hours, `SameSite` is `Lax`, and it is not marked `Secure` because the current site is HTTP. `POST /logout` clears it. `GET /me` returns the signed-in user, role, and groups.

The cookie payload is `user_id|expiry|hmac`. The HMAC key is `SESSION_SECRET`. A changed or expired cookie is treated as signed out.

## Where the bytes live

Three stores, on purpose:

| Store | Holds | Does not hold |
|---|---|---|
| S3 bucket, prefix `docs/` | Original files dropped in the bucket, plus optional `_metadata.json` | Embeddings |
| S3 prefix `documents/{id}/` | Files uploaded through the API | |
| RDS Postgres with pgvector | Document rows, chunk text, access lists, embeddings, users | The original PDF bytes |
| Redis, queue name `ingest` | Background jobs | Anything that must survive a restart. Redis is started with no persistence and a 64 MB cap |

Deleting a document always deletes its chunks. The S3 object is removed only when the key starts with `documents/`. A file that was imported from `docs/` stays in the bucket.

The same file bytes are not indexed twice. The SHA-256 of the file is unique. A repeat upload returns the existing document id. A file whose status is `failed` is queued again.

Allowed uploads are `.pdf`, `.md`, `.txt`, `.html`, `.htm`, and `.csv`, up to `MAX_UPLOAD_MB` (default 20).

## Ingest flow

Uploading a file, or syncing the bucket, only registers a row and enqueues a job. The API returns `202` with status `queued`. The RQ worker does the slow work.

```
file in S3 or API upload
        |
        v
documents row  (status: queued, blob_key, sha256, access, metadata)
        |
        v
Redis job  ingest_document(doc_id)   retries: 10s, 30s, 60s, then the failed registry
        |
        v
status = processing
        |
        +-- load     download the object, extract text, OCR scanned pages with Tesseract
        +-- redact   strip emails, phone numbers, and card-like numbers before they are indexed
        +-- chunk    structure-aware chunks, about CHUNK_MAX_TOKENS (default 250)
        +-- embed    one vector per chunk, tagged with the model fingerprint
        +-- index    in one transaction: delete this document's old chunks, insert the new ones
        |
        v
status = indexed
```

A crash in the middle of the transaction leaves the previous chunks in place. The job runs again from the start. After three failures the document status is `failed` and the `error` column has a short message. The UI shows that status on the document row.

`POST /s3/sync` lists `S3_PREFIX` (default `docs/`). It skips names that start with `_` or `.`, so `_metadata.json` is metadata rather than a document. For each file it merges three sources, later ones winning:

1. Built-in defaults (title from the filename, access `public`, status `active`, trust `trusted`).
2. The sidecar object `{prefix}_metadata.json`, keyed by filename. This is the same shape as `data/raw/_metadata.json`.
3. S3 object user-metadata (`access`, `title`, `department`, `status`, `trust`, `version`, `doc_type`).

An object whose ETag has not changed, and whose status is not `failed`, is reported as `unchanged` and is not re-embedded. The sync response counts `imported`, `updated`, `requeued`, `unchanged`, `skipped`, `duplicate`, and `empty`.

Sample files for this corpus live in `data/raw/`. Their access rules are in `data/raw/_metadata.json`. Salary bands are `hr`. The error runbook, leave policy, tickets, travel policy, and vendor guide are `employees`. The 2024 refund policy is `archived`. Community tips are `untrusted`.

## Query flow

`POST /query` takes a question (2 to 2000 characters), `top_k` (1 to 20, default 5), and `include_archived`.

```
session cookie  -->  groups for that user
        |
        v
embed the question with the same model that indexed the chunks
        |
        v
one SQL statement in app/db.py
    vector search (pgvector cosine, HNSW)
    + Postgres full-text search
    fused with reciprocal rank fusion
    filtered to: access overlaps the user's groups
                 injection_flag is false
                 embedding_model matches the running model
                 status is not archived, unless include_archived is true
        |
        v
optional cross-encoder rerank (off in the deployed compose file)
        |
        v
keep top_k hits
        |
        v
answer()
    if an LLM key is configured: grounded prompt, citations like [S1]
    if no LLM key: quote the source sentence that best matches the question
    if nothing is close enough: abstain
        |
        v
JSON: answer, abstained, mode, citations, trace_id, timings_ms, total_ms
```

Chunks flagged as prompt injection are stored, so an admin can see that they existed, and they are excluded from search.

`mode` is `extractive` when the answer is a quotation from a chunk, `llm` when a model wrote it, and `abstain` when the system refused. The deployed environment has no LLM key, so answers are extractive. The health payload reports the model as `LLM(none)`.

Citations point at a source id (`S1`), a title, and a page when the extractor found one. The UI lists them under the answer.

Archived documents, such as the 2024 refund policy, are hidden unless the Ask form sets **Archived documents** to Include.

## Code map

| Concern | Where to read |
|---|---|
| HTTP routes and the session checks | `app/api.py` |
| Password hashing, cookies, role groups | `app/auth.py` |
| Tables, pgvector setup, hybrid SQL | `app/db.py` |
| The background job | `app/worker.py`, `app/jobs.py` |
| S3 listing and metadata merge | `app/s3_sync.py`, `app/storage.py` |
| Environment variables | `app/settings.py`, `.env.example` |
| Extraction, OCR, cleaning | `rag_core/loaders.py`, `rag_core/cleaning.py` |
| Chunking | `rag_core/chunking.py` |
| Embeddings (`fastembed`, `wordllama`, `openai`, `hashing`) | `rag_core/embeddings.py` |
| Hybrid search and rerank | `rag_core/hybrid.py`, `rag_core/rerank.py` |
| Answers, citations, abstain | `rag_core/generate.py`, `rag_core/prompts.py` |
| PII and injection checks | `rag_core/security.py` |
| The in-memory pipeline used by tests | `rag_core/pipeline.py` |

`rag_core/pipeline.py` is the same design running against an in-memory index. The production path uses Postgres and the worker instead of that in-memory index. Tests use the `hashing` embedder so they do not download a model.

Interactive API docs are at `http://<server>:8000/docs`.
