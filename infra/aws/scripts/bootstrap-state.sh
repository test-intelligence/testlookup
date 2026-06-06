#!/usr/bin/env bash
###############################################################################
# bootstrap-state.sh — create the Terraform remote-state backend (one-time).
#
# Creates an encrypted, versioned, private S3 bucket for state + a DynamoDB
# table for state locking. Idempotent: safe to re-run. Needs AWS credentials
# with S3 + DynamoDB create permissions.
#
# Usage:
#   AWS_PROFILE=tl-admin AWS_REGION=us-east-1 ./bootstrap-state.sh
#
# Prints the TF_STATE_BUCKET / TF_LOCK_TABLE values to export for plan.sh.
###############################################################################
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
LOCK_TABLE="${TF_LOCK_TABLE:-testlookup-tflock}"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="${TF_STATE_BUCKET:-testlookup-tfstate-${ACCOUNT_ID}}"

echo "[bootstrap] account=$ACCOUNT_ID region=$REGION bucket=$BUCKET lock=$LOCK_TABLE"

# ── S3 state bucket ─────────────────────────────────────────────────────────
if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  echo "[bootstrap] bucket exists"
else
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION"
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration LocationConstraint="$REGION"
  fi
fi
aws s3api put-bucket-versioning --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket "$BUCKET" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"aws:kms"},"BucketKeyEnabled":true}]}'
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

# ── DynamoDB lock table ─────────────────────────────────────────────────────
if aws dynamodb describe-table --table-name "$LOCK_TABLE" --region "$REGION" >/dev/null 2>&1; then
  echo "[bootstrap] lock table exists"
else
  aws dynamodb create-table --table-name "$LOCK_TABLE" --region "$REGION" \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST
fi

cat <<EOF

[bootstrap] done. Export these for plan.sh / deploy.sh:

  export TF_STATE_BUCKET="$BUCKET"
  export TF_LOCK_TABLE="$LOCK_TABLE"
  export AWS_REGION="$REGION"
EOF
