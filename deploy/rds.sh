#!/bin/bash
# Create a private Postgres instance for pgvector, in the same VPC as the app EC2.
# Free-tier shape: db.t3.micro, 20 GB, one availability zone. That allowance is for the
# first 12 months of an account, and it is not an always-free database.
#
#   RDS_PASSWORD='choose-a-long-password' bash deploy/rds.sh
#
# If RDS_PASSWORD is omitted, a random one is generated and printed once.
# The script does nothing except print the endpoint when the instance "rag" already exists.
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
INSTANCE_ID="${INSTANCE_ID:-i-071ee575df0c00009}"
DB_ID="${DB_ID:-rag}"
DB_USER="${DB_USER:-rag}"
DB_NAME="${DB_NAME:-postgres}"
DB_CLASS="${DB_CLASS:-db.t3.micro}"
SUBNET_GROUP="${SUBNET_GROUP:-rag-subnets}"
DB_SG_NAME="${DB_SG_NAME:-rag-db}"

existing=$(aws rds describe-db-instances --region "$AWS_REGION" \
  --db-instance-identifier "$DB_ID" \
  --query "DBInstances[0].Endpoint.Address" --output text 2>/dev/null || true)
if [ -n "$existing" ] && [ "$existing" != "None" ]; then
  echo "RDS ${DB_ID} already exists: ${existing}"
  echo "DATABASE_URL=postgresql://${DB_USER}:<password>@${existing}:5432/${DB_NAME}?sslmode=require"
  exit 0
fi

if [ -z "${RDS_PASSWORD:-}" ]; then
  RDS_PASSWORD=$(python -c "import secrets,string; alphabet=string.ascii_letters+string.digits; print(''.join(secrets.choice(alphabet) for _ in range(24)))")
  GENERATED=1
else
  GENERATED=0
fi

vpc=$(aws ec2 describe-instances --region "$AWS_REGION" --instance-ids "$INSTANCE_ID" \
  --query "Reservations[0].Instances[0].VpcId" --output text)
ec2_sg=$(aws ec2 describe-instances --region "$AWS_REGION" --instance-ids "$INSTANCE_ID" \
  --query "Reservations[0].Instances[0].SecurityGroups[0].GroupId" --output text)
mapfile -t subnets < <(aws ec2 describe-subnets --region "$AWS_REGION" \
  --filters "Name=vpc-id,Values=${vpc}" --query "Subnets[].SubnetId" --output text | tr '\t' '\n')
if [ "${#subnets[@]}" -lt 2 ]; then
  echo "VPC ${vpc} needs two subnets for an RDS subnet group." >&2
  exit 1
fi

if ! aws rds describe-db-subnet-groups --region "$AWS_REGION" --db-subnet-group-name "$SUBNET_GROUP" >/dev/null 2>&1; then
  aws rds create-db-subnet-group --region "$AWS_REGION" \
    --db-subnet-group-name "$SUBNET_GROUP" \
    --db-subnet-group-description "Private subnets for the RAG database" \
    --subnet-ids "${subnets[@]}"
fi

db_sg=$(aws ec2 describe-security-groups --region "$AWS_REGION" \
  --filters "Name=vpc-id,Values=${vpc}" "Name=group-name,Values=${DB_SG_NAME}" \
  --query "SecurityGroups[0].GroupId" --output text 2>/dev/null || true)
if [ -z "$db_sg" ] || [ "$db_sg" = "None" ]; then
  db_sg=$(aws ec2 create-security-group --region "$AWS_REGION" \
    --group-name "$DB_SG_NAME" --description "Postgres for the RAG app" --vpc-id "$vpc" \
    --query "GroupId" --output text)
  aws ec2 authorize-security-group-ingress --region "$AWS_REGION" \
    --group-id "$db_sg" --protocol tcp --port 5432 --source-group "$ec2_sg"
fi

engine_version=$(aws rds describe-db-engine-versions --region "$AWS_REGION" --engine postgres \
  --query "reverse(sort_by(DBEngineVersions[?starts_with(EngineVersion, '16.')], &EngineVersion))[0].EngineVersion" \
  --output text)

echo "Creating ${DB_ID} (${DB_CLASS}, postgres ${engine_version}) in ${vpc}. This takes several minutes."
aws rds create-db-instance --region "$AWS_REGION" \
  --db-instance-identifier "$DB_ID" \
  --db-instance-class "$DB_CLASS" \
  --engine postgres \
  --engine-version "$engine_version" \
  --master-username "$DB_USER" \
  --master-user-password "$RDS_PASSWORD" \
  --db-name "$DB_NAME" \
  --allocated-storage 20 \
  --storage-type gp2 \
  --storage-encrypted \
  --db-subnet-group-name "$SUBNET_GROUP" \
  --vpc-security-group-ids "$db_sg" \
  --no-publicly-accessible \
  --no-multi-az \
  --backup-retention-period 1 \
  --no-deletion-protection

aws rds wait db-instance-available --region "$AWS_REGION" --db-instance-identifier "$DB_ID"
endpoint=$(aws rds describe-db-instances --region "$AWS_REGION" --db-instance-identifier "$DB_ID" \
  --query "DBInstances[0].Endpoint.Address" --output text)

echo
echo "RDS is available: ${endpoint}"
echo "Put this in /opt/rag/.env and restart the API and worker:"
echo "DATABASE_URL=postgresql://${DB_USER}:${RDS_PASSWORD}@${endpoint}:5432/${DB_NAME}?sslmode=require"
if [ "$GENERATED" = "1" ]; then
  echo "That password was generated for this run. It is not saved anywhere else."
fi
