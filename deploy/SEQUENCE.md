# Deploy order

Run these from a machine that has the AWS CLI and credentials, except the steps marked **on the EC2 instance**. The instance `i-071ee575df0c00009` must already be running. RDS is placed in that instance's VPC, and Redis is a container on that instance.

## Create

1. `bash deploy/s3.sh`  
   Creates the private bucket `enterpise-rag` if it is missing.

2. `RDS_PASSWORD='choose-a-long-password' bash deploy/rds.sh`  
   Creates the private Postgres instance `rag` and prints `DATABASE_URL`. If `rag` already exists, it only prints the endpoint.

3. On the instance, put the repo at `/opt/rag` and write `/opt/rag/.env`.  
   Set `DATABASE_URL` from step 2, plus `S3_BUCKET`, `AWS_REGION`, `S3_PREFIX`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `SESSION_SECRET`, and `INTERNAL_API_TOKEN`. Copy `.env.example`.

4. **On the EC2 instance:** `bash deploy/redis.sh`  
   Starts the Redis container. Requires `/opt/rag/.env`.

5. **On the EC2 instance:** `bash deploy/update.sh`  
   Builds the API and worker, starts every container, and waits until `/health` answers.

6. Load the corpus, from the laptop:

```bash
aws s3 sync data/raw s3://enterpise-rag/docs/ --region us-east-1
```

Then sign in as admin and use **Sync**, or `POST /s3/sync`.

7. `bash deploy/github-oidc-role.sh`  
   Once, before the first GitHub Actions deploy. Later pushes to `main` run the tests and then step 5 by themselves. They do not replace `/opt/rag/.env`.

## Delete

Nothing is deleted unless `CONFIRM=delete` is set. These commands do not terminate the EC2 instance.

1. **On the EC2 instance:** `CONFIRM=delete bash deploy/redis-delete.sh`  
   Stops and removes the Redis container.

2. `CONFIRM=delete bash deploy/delete.sh`  
   Empties and deletes the S3 bucket, then deletes the RDS instance with no final snapshot.

To remove one store only, use `deploy/s3-delete.sh` or `deploy/rds-delete.sh` with `CONFIRM=delete`.
