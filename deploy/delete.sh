#!/bin/bash
# Tear down the RAG data stores created by deploy/s3.sh and deploy/rds.sh.
#
#   CONFIRM=delete bash deploy/delete.sh
#
# This deletes the S3 bucket (and every object in it) and the RDS instance.
# It does not terminate the EC2 instance. That machine also runs other software,
# and the Redis container has to be removed on the instance itself:
#
#   CONFIRM=delete bash deploy/redis-delete.sh
set -euo pipefail

if [ "${CONFIRM:-}" != "delete" ]; then
  echo "Refusing to delete the deployment. Re-run with CONFIRM=delete." >&2
  exit 1
fi

root=$(cd "$(dirname "$0")" && pwd)
CONFIRM=delete bash "$root/s3-delete.sh"
CONFIRM=delete bash "$root/rds-delete.sh"
echo
echo "S3 and RDS are deleted. On the EC2 instance, remove the queue with:"
echo "  CONFIRM=delete bash deploy/redis-delete.sh"
