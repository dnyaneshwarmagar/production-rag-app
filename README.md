# Nimbu Mart RAG API

Production retrieval API. Documents live in S3. Chunks and embeddings live in RDS Postgres with pgvector. Redis runs the ingestion queue.

```bash
docker compose -f docker-compose.aws.yml up -d --build
```

Set `DATABASE_URL`, `S3_BUCKET`, and `AWS_REGION` in `.env` first. See `.env.example`.

Put source files in `s3://<bucket>/docs/`, then index them:

```bash
curl -X POST http://127.0.0.1:8000/s3/sync
```

Ask a question:

```bash
curl -X POST http://127.0.0.1:8000/query \
  -H "Content-Type: application/json" \
  -d "{\"question\":\"How many days do I have to return a phone?\"}"
```

API docs: http://127.0.0.1:8000/docs

`deploy/ec2-bootstrap.sh` installs Docker on Amazon Linux 2023 and starts this compose file. The EC2 instance role needs read/write on the bucket, and the metadata hop limit must be 2 so the containers can use that role.

GitHub Actions (`.github/workflows/ci.yml`) runs the test suite on every pull request. A push to `main` then copies the repo to the EC2 instance and rebuilds the containers. The server `.env` is not part of that copy. The deploy role is `github-rag-deploy`, created with `bash deploy/github-oidc-role.sh`.
