#!/bin/bash
# Empty the document bucket and delete it. Every original file in the bucket is removed.
#
#   CONFIRM=delete bash deploy/s3-delete.sh
set -euo pipefail

if [ "${CONFIRM:-}" != "delete" ]; then
  echo "Refusing to delete the bucket. Re-run with CONFIRM=delete." >&2
  exit 1
fi

AWS_REGION="${AWS_REGION:-us-east-1}"
S3_BUCKET="${S3_BUCKET:-enterpise-rag}"

if ! aws s3api head-bucket --bucket "$S3_BUCKET" --region "$AWS_REGION" >/dev/null 2>&1; then
  echo "No bucket named ${S3_BUCKET}."
  exit 0
fi

echo "Deleting every object in s3://${S3_BUCKET}"
aws s3 rm "s3://${S3_BUCKET}" --region "$AWS_REGION" --recursive
aws s3api delete-bucket --bucket "$S3_BUCKET" --region "$AWS_REGION"
echo "Deleted s3://${S3_BUCKET}"
