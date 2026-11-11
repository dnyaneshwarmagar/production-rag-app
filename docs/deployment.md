# Deployment

The running system is one EC2 instance in the same VPC as a private RDS Postgres database. Documents are in S3. Redis, the API, and the ingestion worker are Docker containers on that instance.

```
browser  -- :8000 -->  EC2
                         rag-api-1      FastAPI
                         rag-worker-1   RQ worker
                         rag-redis-1    queue only
                              |
                              +--> RDS Postgres (private, pgvector)
                              +--> S3 bucket enterpise-rag
```

GitHub Actions, on a push to `main`, runs the tests and then copies the repository onto the instance and rebuilds the containers. The server's `.env` is not in git and is not overwritten by that copy.

## What is already provisioned

These are the resources the workflow and the compose file are aimed at. Confirm them in the AWS console if the instance has been replaced.

| Resource | Value |
|---|---|
| Region | `us-east-1` |
| Account | `815398003681` |
| EC2 | `i-071ee575df0c00009`, name `hr-assistant`, type `t3.micro` (1 GB RAM, 2 GB swap), Ubuntu, user `ubuntu`. Public address: `52.90.80.5`. A stop/start can change the address. CPU credits are `standard`, so a long build slows down instead of billing extra credit. |
| App directory on the instance | `/opt/rag` |
| RDS | Identifier `rag`, user `rag`, database name `postgres`, port 5432, `sslmode=require`, publicly accessible **off**. Security group allows 5432 from the EC2 security group only. |
| S3 | Bucket `enterpise-rag` (that spelling), prefix `docs/` |
| Instance role | `rag-app-s3`, allowed to list the bucket and get, put, and delete objects in it. Instance metadata hop limit is 2 so the containers can use this role. |
| GitHub repository | `dnyaneshwarmagar/production-rag-app` |
| Deploy role | `github-rag-deploy` |

Postgres is not reachable from a laptop. Anything that needs `DATABASE_URL` runs on the EC2 instance. Port 8000 is published on the instance; the security group may allow it only from specific addresses, so a new network needs that rule updated before the browser can open the UI. Port 22 is open so GitHub-hosted runners, whose addresses change, can SSH.

## Configuration

`.env.example` is the template. The real `.env` is gitignored. On the server it lives at `/opt/rag/.env` and is passed into the API and worker with Compose `env_file`.

Values that must be set for this deployment:

| Variable | What to put |
|---|---|
| `DATABASE_URL` | `postgresql://rag:<password>@<rds-endpoint>:5432/postgres?sslmode=require` |
| `S3_BUCKET` | `enterpise-rag` |
| `AWS_REGION` | `us-east-1` |
| `S3_PREFIX` | `docs/` |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Used once, to insert the first admin if that username is missing. |
| `SESSION_SECRET` | Long random string. Changing it signs everyone out. |
| `INTERNAL_API_TOKEN` | Long random string. Lets `scripts/ask.py` and the evaluation script pass groups. |
| `LANGSMITH_API_KEY` | Optional. Empty means tracing stays local. |
| `LANGSMITH_PROJECT` | `nimbu-mart` |

`docker-compose.aws.yml` overrides two settings for every container, regardless of `.env`:

* `REDIS_URL=redis://redis:6379/0` so the app talks to the Redis container, not a URL written for a laptop.
* `EMBEDDER=wordllama` and `USE_RERANKER=0` so the instance does not download the larger fastembed and reranker models. The health check reports the model as `wordllama-l2-supercat-256:256`.

Do not put AWS access keys in `.env` when the instance role is attached. The containers read the role through instance metadata.

## Containers

From `/opt/rag`:

```bash
sudo docker compose -f docker-compose.aws.yml up -d --build
sudo docker compose -f docker-compose.aws.yml ps
```

| Service | Image | Command |
|---|---|---|
| `redis` | `redis:7-alpine` | No disk persistence, 64 MB, evicts least-recently-used keys. |
| `api` | Built from the repo `Dockerfile` | `uvicorn app.api:app --host 0.0.0.0 --port 8000` |
| `worker` | Same image | `rq worker ingest --with-scheduler --worker-class rq.worker.SimpleWorker` |

The image is `python:3.12-slim` plus Tesseract, then `requirements.txt`, then `rag_core/`, `app/`, and `eval/`. On startup the API process runs `migrate()`, which creates the `vector` extension and the tables, then inserts the bootstrap admin if needed. The first boot after a fresh database is the moment pgvector appears. Creating the extension and registering the vector type are two connections; the code does the extension first.

The API healthcheck hits `http://127.0.0.1:8000/health` and allows a 90 second start period because the embedding model loads on import.

`deploy/update.sh` is what CI runs after it unpacks a new tree: compose up, then curl `/health` for up to about three minutes. The instance is Ubuntu. Docker and the Compose plugin are already installed, and `sudo` is required to talk to the Docker socket.

The full create and delete order is [deploy/SEQUENCE.md](../deploy/SEQUENCE.md).

## Component scripts

These create or remove one piece of the stack. Create scripts are safe to run again. Delete scripts do nothing unless you set `CONFIRM=delete`. They do not terminate the EC2 instance.

| Piece | Create | Delete |
|---|---|---|
| S3 bucket | `bash deploy/s3.sh` | `CONFIRM=delete bash deploy/s3-delete.sh` |
| RDS Postgres | `RDS_PASSWORD='...' bash deploy/rds.sh` | `CONFIRM=delete bash deploy/rds-delete.sh` |
| Redis | `bash deploy/redis.sh` on the EC2 instance | `CONFIRM=delete bash deploy/redis-delete.sh` on the instance |
| S3 and RDS together | | `CONFIRM=delete bash deploy/delete.sh` |

Redis is the Docker container from `docker-compose.aws.yml`, not ElastiCache. `deploy/rds.sh` builds a private `db.t3.micro` in the same VPC as the app instance and prints a `DATABASE_URL` for `/opt/rag/.env`.

## First-time data load

The corpus in `data/raw/` is not read by the running API. It has to be in the bucket.

From a machine with AWS credentials that can write the bucket:

```bash
aws s3 sync data/raw s3://enterpise-rag/docs/ --region us-east-1
```

Then, as an admin (the UI **Sync** button, or `POST /s3/sync` with the session cookie or the internal token), index the prefix. The worker moves each new file from `queued` to `indexed`. `_metadata.json` is uploaded too and supplies access groups; it is not indexed as a document.

Uploading a single file through the UI stores it under `documents/{id}/` and enqueues the same job.

## CI/CD

Workflow file: `.github/workflows/ci.yml`.

```
pull request  -->  test job only
push to main  -->  test job, then deploy job
manual run    -->  test job, then deploy job, when started from main
```

**Test job.** Ubuntu runner, Python 3.12, `pip install -r requirements.txt`, `pytest` with `EMBEDDER=hashing` and `LLM_PROVIDER=none`. No cloud credentials. A failure here skips deploy.

**Deploy job.**

1. GitHub issues an OIDC token whose subject is `repo:dnyaneshwarmagar@89983890/production-rag-app@1452846802:ref:refs/heads/main`. The numbers are the GitHub user id and repository id. The role trust policy has to match that string, not the name without ids.
2. AWS lets that token assume `arn:aws:iam::815398003681:role/github-rag-deploy` for a short time. The role can describe the instance and call `ec2-instance-connect:SendSSHPublicKey` for user `ubuntu` on `i-071ee575df0c00009`. It cannot read the database or the bucket.
3. The runner creates a one-off SSH key, registers the public half on the instance (it works for about 60 seconds), and streams a tar of the repo to `/opt/rag`. The tar excludes `.git`, `.env`, virtualenvs, caches, logs, and `data/processed`.
4. On the instance, `bash deploy/update.sh` rebuilds and restarts the containers, then curls `http://127.0.0.1:8000/health`.

No AWS access key and no long-lived SSH private key are stored in GitHub. The role was created once with `deploy/github-oidc-role.sh`. Re-running that script updates the same role. `OWNER_ID` and `REPO_ID` in that script must stay `89983890` and `1452846802` unless the GitHub identity changes.

Two deploys cannot run at once (`concurrency: production-ec2`).

### See whether a run worked

The workflow runs are at:

https://github.com/dnyaneshwarmagar/production-rag-app/actions

A run can have more than one attempt. The first attempt failed because the role expected the subject without the numeric ids. Later attempts, after the trust policy was updated, completed. If the page still shows a red attempt, switch to the latest attempt.

On the instance, a good deploy looks like:

```bash
sudo docker compose -f docker-compose.aws.yml ps
curl -sS http://127.0.0.1:8000/health
```

`/health` returns the embedding model, `langsmith` true or false, the S3 URL, the pgvector version, the indexed chunk count, and the number of jobs waiting in Redis. From a browser that the security group allows, open `http://<public-ip>:8000/` and sign in.

## What a deploy does not do

* It does not replace `/opt/rag/.env`. New variables have to be added on the server, then the containers restarted.
* It does not delete files on the server that were removed from git. Unpacking a tar only adds and overwrites.
* It does not re-index S3. Existing chunks stay. A new corpus still needs `POST /s3/sync` or an upload.
* It does not open port 8000 to the world.

## When something fails

| Symptom | Where to look |
|---|---|
| `Not authorized to perform sts:AssumeRoleWithWebIdentity` | The OIDC subject from GitHub does not match the role trust policy. CloudTrail on `AssumeRoleWithWebIdentity` shows the subject that was sent. Update `deploy/github-oidc-role.sh` and the role. |
| SSH fails immediately after the key is sent | The public key is only valid for about a minute, and port 22 must accept the runner. The workflow sends the key after the tar is built, then connects once. |
| `permission denied` on the Docker socket | On this Ubuntu host the deploy user needs `sudo docker`. `deploy/update.sh` already uses sudo. |
| API restarts and logs `vector type not found` | The extension must be created on a connection that has not yet registered the pgvector type. `app/db.py` does that. Do not register the type before `CREATE EXTENSION vector`. |
| `/health` never returns during deploy | `deploy/update.sh` prints the last 60 lines of the API log. Typical causes are a bad `DATABASE_URL`, the RDS security group, or the model still loading. Give it the full three minutes before treating it as down. |
| Browser cannot open the site, health works on the instance | The security group for port 8000 does not include your current address. |
| Sign-in works, LangSmith stays off | `LANGSMITH_API_KEY` is empty in the server `.env`, or the containers were not restarted after it was set. |
| Documents stuck on `queued` | Redis or the worker is down. `sudo docker compose -f docker-compose.aws.yml logs --tail 100 worker`. |
| A document is `failed` | The `error` column and the worker log. Scanned PDFs need Tesseract, which is installed in the image. |

## Local checks before you push

CI will run the tests, but you can run the same command from the repo root:

```bash
python -m pytest -q
```

Set `EMBEDDER=hashing` if your `.env` points at a model you do not want the tests to load. The tests set it themselves when they import.

Do not commit `.env`, `venv/`, logs, or `__pycache__/`. `.gitignore` already excludes them. `.env.example` is the file that belongs in git.
