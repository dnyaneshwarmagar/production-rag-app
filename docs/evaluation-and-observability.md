# Evaluation and observability

Two different questions:

* **Evaluation** asks whether the system found the right passages and said the right thing, on a fixed set of questions.
* **Observability** asks what one live request did: which stages ran, how long they took, and whether a stage failed.

They share the golden questions only when you choose to. Tracing runs on every query and every ingest. Scoring runs when you ask it to.

## The golden set

`eval/golden.jsonl` is 33 questions, one JSON object per line. Each object looks like this:

```json
{
  "id": "q01",
  "question": "How many days do I have to return a mobile phone?",
  "type": "semantic",
  "relevant_docs": ["refund_policy_2025.md", "faq_hindi.md"],
  "evidence": ["7 days from delivery", "7 दिनों"],
  "answer_must_contain": ["7 days"],
  "answerable": true
}
```

| Field | Meaning |
|---|---|
| `type` | Why this question is in the set: `semantic`, `keyword`, `table`, `reasoning`, `ocr`, `acl`, `unanswerable`, `versioning`, `multilingual`. |
| `relevant_docs` | Filenames that contain the answer. |
| `evidence` | Strings that must appear in a chunk before that chunk counts as relevant. |
| `answer_must_contain` | Facts the answer text must include. Digits are compared with thousands-separators removed, so `5,000` matches `5000`. |
| `answerable` | `false` means a correct system refuses to answer. |
| `user_groups` | Optional. The groups to search as. Missing means the caller should use `public`, `employees`, and `hr`. |
| `include_archived` | Optional. Question `q32` sets this so the archived 2024 policy can be found. |
| `forbidden_docs` | Present on the ACL question that an employee must not answer from the salary file. |

Questions worth knowing before you read a score:

| Id | What it checks |
|---|---|
| `q01`–`q05` | Current refund policy: 7 days for phones, 15 days for shoes, pickup fee, card refund timing. |
| `q06`–`q08` | Error codes `E102`, `E110`, `E103`. These need the employee group. |
| `q19`–`q21` | Travel policy PDF. Employee group. |
| `q22`–`q25` | Scanned warranty PDF and the mixed vendor PDF. OCR has to have worked at ingest time. |
| `q28` | Same salary question as an employee. `answerable` is false. The salary file is forbidden. |
| `q29` | Same salary question with the `hr` group. The answer should contain the L5 band. |
| `q30`, `q31` | Unanswerable (delivery to Mars, today's stock price). The system should abstain. |
| `q32` | The archived 2024 policy, which is hidden unless archived search is on. |
| `q33` | Hindi question about cash on delivery. |

A chunk is **relevant** for a question when its document filename is in `relevant_docs` and, if `evidence` is non-empty, at least one evidence string appears in the chunk text. That rule is `is_relevant` in `rag_core/evaluation.py`. It does not depend on how the file was chunked.

## Retrieval metrics

`retrieval_metrics` scores one ranked list. `evaluate_retrieval` averages that over the answerable golden questions and also breaks the average down by `type`.

| Metric | Reading |
|---|---|
| `hit@k` | 1 if any relevant chunk is in the top k, else 0. |
| `recall@k` | Relevant chunks in the top k, divided by all relevant chunks in the index. |
| `precision@k` | Relevant chunks in the top k, divided by k. |
| `mrr` | 1 divided by the rank of the first relevant chunk. 0 if none. |
| `ndcg@k` | Rewards a relevant chunk more when it is nearer the top. |
| `context_precision` | Are the relevant chunks ranked above the irrelevant ones? |

Unanswerable questions are skipped here, because there is nothing to find.

These numbers need the chunk text and the **filename**. `POST /query` returns the answer and citation titles, not the retrieved chunk bodies, and the chunk's `doc_id` in Postgres is a UUID. A retrieval eval has to join `documents.filename` and treat that filename as the document id, the same way `app/reembed.py` does when it builds `Chunk("x", filename, text)`. The database is private, so this eval runs on the EC2 instance, inside the API container, where `DATABASE_URL` works.

`python -m app.reembed` prints a shadow `hit@5` while it copies embeddings into a new column. That command also rewrites vectors. Use it when you are changing the embedding model, not as the everyday score.

## Answer metrics

`evaluate_answers` calls an `ask_fn` that returns an `Answer` (`rag_core/generate.py`) and reports:

| Metric | A point is awarded when |
|---|---|
| `correct` | An answerable question contains every `answer_must_contain` string and did not abstain. An unanswerable question abstained. |
| `abstain_ok` | The system abstained exactly when the question was unanswerable. |
| `cited` | An answered question has at least one citation and no citation that points at a source the model invented. |
| `faithfulness_heuristic` | Share of answer sentences whose content words mostly appear in the retrieved context. This is word overlap. It will not catch a subtle wrong number that happens to use the same words. |

`llm_judge` can add a second opinion from a model, returning faithfulness, relevance, and a one-line reason. The deployed app has no LLM key, so the live score is the heuristic plus `answer_must_contain`.

### Score the live API

From a machine that can reach port 8000, with the repo checked out and `INTERNAL_API_TOKEN` set in `.env`:

```powershell
@'
import json, urllib.request
from pathlib import Path
from rag_core.evaluation import load_golden, answer_correct

env = {}
for line in Path(".env").read_text(encoding="utf-8").splitlines():
    if line.strip() and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"')
api = "http://<server>:8000"
golden = load_golden()
rows = []
for item in golden:
    groups = ",".join(item.get("user_groups") or ["public", "employees", "hr"])
    body = json.dumps({
        "question": item["question"],
        "include_archived": bool(item.get("include_archived")),
        "top_k": 5,
    }).encode()
    req = urllib.request.Request(api + "/query", data=body, headers={
        "Content-Type": "application/json",
        "X-Internal-Token": env["INTERNAL_API_TOKEN"],
        "X-User-Groups": groups,
    })
    result = json.load(urllib.request.urlopen(req, timeout=120))
    answerable = item.get("answerable", True)
    correct = (answer_correct(result["answer"], item) and not result["abstained"]) if answerable else result["abstained"]
    rows.append(correct)
    print(f"{item['id']} {'ok' if correct else 'MISS'} {item['type']} abstain={result['abstained']}")
print(f"correct {sum(rows) / len(rows):.3f}  n={len(rows)}")
'@ | python -
```

Replace `<server>` with the instance address. The internal token is what lets each question use its own groups. Question `q28` must be asked as `public,employees` or the salary refusal is meaningless. Question `q32` must send `include_archived: true`.

This score checks the answer text. It does not compute hit@k or nDCG.

## What CI runs instead

`.github/workflows/ci.yml` runs `python -m pytest -q` with `EMBEDDER=hashing` and `LLM_PROVIDER=none`. That is 26 tests. They do not call AWS, RDS, or the live API.

| File | What it locks in |
|---|---|
| `tests/test_core.py` | Cleaning, chunking, BM25, rank fusion, filters, PII, injection detection, the context budget, ACL, quarantine, archived documents, and a keyword lookup. |
| `tests/test_auth.py` | Password hashes, session cookies, the sign-up ban on `admin`, and the role-to-group table. |
| `tests/test_s3_sync.py` | Prefix normalization, which keys are importable, and how sidecar metadata merges with object metadata. |
| `tests/test_langsmith.py` | Tracing stays off without a key, and spans are recorded on a fake client with no network call. |

A green CI run means those behaviors still hold. It does not mean the 33 golden questions were scored against production.

## Observability

Every query and every ingest builds a local trace in `rag_core/observability.py`:

* a 12-character `trace_id`
* one span per stage, with its duration in milliseconds
* attributes the stage adds, such as candidate count, token counts, or the number of chunks

`POST /query` returns `trace_id`, `timings_ms`, and `total_ms`. The API also writes one JSON line to the `uvicorn.error` log. The browser shows the trace id under the answer.

Spans you will see:

| Trace | Spans |
|---|---|
| `query` | `embed_query`, `hybrid_search`, `rerank` (only if a reranker is on), `generate` |
| `ingest` | `load`, `redact`, `chunk`, `embed`, `index` |

The deployed compose file sets `USE_RERANKER=0`, so query traces have no rerank span.

### LangSmith

When `LANGSMITH_API_KEY` is set, the same trace is sent to [LangSmith](https://smith.langchain.com). Leave the key empty and nothing is sent. `LANGSMITH_TRACING=false` turns it off even if the key is present.

| Variable | Purpose |
|---|---|
| `LANGSMITH_API_KEY` | API key from LangSmith → Settings → API keys. |
| `LANGSMITH_PROJECT` | Project name. Default `nimbu-mart`. The project is created on the first trace. |
| `LANGSMITH_TRACING` | Optional. `false` disables export. |
| `LANGSMITH_ENDPOINT` | Optional. Defaults to the LangSmith cloud API. |

Each query or ingest is one root run. Each span is a child run, tagged with the same `trace_id` that the API returned, so you can paste that id into the LangSmith filter `metadata.trace_id`. Run types are `embedding` for embed steps, `retriever` for search and rerank, `llm` for generate, `parser` for load, and `chain` for the rest. Runs are tagged `nimbu-mart`.

The root run's inputs are the question or the document id and the groups. Outputs include the answer, mode, abstain flag, citation titles, chunk counts, and `total_ms`. Embedding vectors are not uploaded.

A LangSmith outage is logged once and the request still succeeds. `GET /health` includes `"langsmith": true` or `false`, and the page's status line shows `langsmith on` or `langsmith off`.

The key has to be in the `.env` on the server. A deploy does not copy `.env`. After you add the key, restart the API and worker containers so they read it. New questions and new ingests then show up under the `nimbu-mart` project. Old requests are not backfilled.

`tests/test_langsmith.py` checks the client calls with a fake run object. It does not need a key and it does not talk to LangSmith.
