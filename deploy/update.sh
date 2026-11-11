#!/bin/bash
# Runs on the EC2 instance after a new tree has been unpacked into /opt/rag.
# .env is not in the archive, so the existing one is left in place.
set -euo pipefail

cd /opt/rag
sudo docker compose -f docker-compose.aws.yml up -d --build
sudo docker compose -f docker-compose.aws.yml ps

for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/health; then
    echo
    exit 0
  fi
  sleep 6
done

echo "API did not become healthy." >&2
sudo docker compose -f docker-compose.aws.yml logs --tail 60 api >&2 || true
exit 1
