#!/bin/bash
# Create the document bucket. Safe to run again: an existing bucket is left as it is.
#
#   bash deploy/s3.sh
#
# Requires the AWS CLI and credentials that can create a bucket in AWS_REGION.
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
S3_BUCKET="${S3_BUCKET:-enterpise-rag}"

if aws s3api head-bucket --bucket "$S3_BUCKET" --region "$AWS_REGION" >/dev/null 2>&1; then
  echo "Bucket already exists: s3://${S3_BUCKET}"
else
  if [ "$AWS_REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$S3_BUCKET" --region "$AWS_REGION"
  else
    aws s3api create-bucket --bucket "$S3_BUCKET" --region "$AWS_REGION" \
      --create-bucket-configuration "LocationConstraint=${AWS_REGION}"
  fi
  echo "Created s3://${S3_BUCKET}"
fi

aws s3api put-public-access-block --bucket "$S3_BUCKET" --region "$AWS_REGION" \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

echo "Public access is blocked. Put corpus files under s3://${S3_BUCKET}/docs/"
echo "The EC2 instance role still needs ListBucket and Get/Put/DeleteObject on this bucket."
