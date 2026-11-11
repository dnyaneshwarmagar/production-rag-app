#!/bin/bash
# Stop and remove the Redis container. Queued ingest jobs are dropped.
# The API and worker stay running, but they cannot enqueue until Redis is started again.
# Run this on the EC2 instance:
#
#   CONFIRM=delete bash deploy/redis-delete.sh
set -euo pipefail

if [ "${CONFIRM:-}" != "delete" ]; then
  echo "Refusing to remove Redis. Re-run with CONFIRM=delete." >&2
  exit 1
fi

cd "$(dirname "$0")/.."
sudo docker compose -f docker-compose.aws.yml stop redis
sudo docker compose -f docker-compose.aws.yml rm -f redis
echo "Redis container removed."
