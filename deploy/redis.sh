#!/bin/bash
# Start Redis for the ingest queue.
#
# Redis is a container on the same EC2 instance as the API. It is not ElastiCache.
# The compose file caps it at 64 MB and does not write it to disk. Run this on the
# instance, from the repo root (on the server that directory is /opt/rag):
#
#   bash deploy/redis.sh
set -euo pipefail

cd "$(dirname "$0")/.."
if [ ! -f .env ]; then
  echo "Missing .env next to docker-compose.aws.yml. Compose will not start without it." >&2
  exit 1
fi

sudo docker compose -f docker-compose.aws.yml up -d redis
sudo docker compose -f docker-compose.aws.yml ps redis
echo "API and worker use REDIS_URL=redis://redis:6379/0 inside the compose network."
