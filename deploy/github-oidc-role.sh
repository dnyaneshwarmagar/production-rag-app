#!/bin/bash
# Lets GitHub Actions (main branch only) open a short-lived SSH session to the app instance.
# Run once from a machine that already has AWS credentials:
#   bash deploy/github-oidc-role.sh
set -euo pipefail

ACCOUNT_ID="${ACCOUNT_ID:-815398003681}"
REGION="${AWS_REGION:-us-east-1}"
REPO="${GITHUB_REPO:-dnyaneshwarmagar/production-rag-app}"
INSTANCE_ID="${INSTANCE_ID:-i-071ee575df0c00009}"
ROLE_NAME="${ROLE_NAME:-github-rag-deploy}"
OIDC_URL="https://token.actions.githubusercontent.com"
PROVIDER_ARN="arn:aws:iam::${ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"

if ! aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$PROVIDER_ARN" >/dev/null 2>&1; then
  aws iam create-open-id-connect-provider \
    --url "$OIDC_URL" \
    --client-id-list sts.amazonaws.com \
    --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 1c58a3a8518e8759bf075b76b750d4f2df264fcd
fi

trust=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"Federated": "${PROVIDER_ARN}"},
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:${REPO}:ref:refs/heads/main"
        }
      }
    }
  ]
}
EOF
)

if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam update-assume-role-policy --role-name "$ROLE_NAME" --policy-document "$trust"
else
  aws iam create-role --role-name "$ROLE_NAME" --assume-role-policy-document "$trust" \
    --description "GitHub Actions deploy for ${REPO}"
fi

policy=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "ec2-instance-connect:SendSSHPublicKey",
      "Resource": "arn:aws:ec2:${REGION}:${ACCOUNT_ID}:instance/${INSTANCE_ID}",
      "Condition": {"StringEquals": {"ec2:osuser": "ubuntu"}}
    },
    {
      "Effect": "Allow",
      "Action": "ec2:DescribeInstances",
      "Resource": "*"
    }
  ]
}
EOF
)

aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name deploy --policy-document "$policy"
echo
echo "Role ARN: arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
echo "Deploy runs from pushes to main. No long-lived SSH key is stored in GitHub."
