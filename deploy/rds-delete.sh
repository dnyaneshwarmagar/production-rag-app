#!/bin/bash
# Delete the Postgres instance. Chunks, users, and embeddings in that database are gone.
# No final snapshot is kept.
#
#   CONFIRM=delete bash deploy/rds-delete.sh
set -euo pipefail

if [ "${CONFIRM:-}" != "delete" ]; then
  echo "Refusing to delete the database. Re-run with CONFIRM=delete." >&2
  exit 1
fi

AWS_REGION="${AWS_REGION:-us-east-1}"
DB_ID="${DB_ID:-rag}"

if ! aws rds describe-db-instances --region "$AWS_REGION" --db-instance-identifier "$DB_ID" >/dev/null 2>&1; then
  echo "No RDS instance named ${DB_ID}."
  exit 0
fi

aws rds modify-db-instance --region "$AWS_REGION" --db-instance-identifier "$DB_ID" \
  --no-deletion-protection --apply-immediately >/dev/null || true
aws rds delete-db-instance --region "$AWS_REGION" --db-instance-identifier "$DB_ID" \
  --skip-final-snapshot --delete-automated-backups
echo "Deleting ${DB_ID}. Waiting until it is gone."
aws rds wait db-instance-deleted --region "$AWS_REGION" --db-instance-identifier "$DB_ID"
echo "Deleted RDS ${DB_ID}."
echo "The subnet group and security group rag-db were left in place so a later create can reuse them."
