#!/usr/bin/env bash
###############################################################################
# plan.sh — read-only Terraform plan for one environment.
#
# This DOES talk to AWS (read-only) to diff desired vs actual state, so it needs
# credentials + the remote state backend. It NEVER applies. Use it as the
# change-review step before deploy.sh.
#
# Usage:
#   AWS_PROFILE=tl-dev ./plan.sh dev
#   ./plan.sh prod                       # uses ambient creds / OIDC role
#
# Requires env vars (or scripts/bootstrap-state.sh output):
#   TF_STATE_BUCKET   S3 bucket for state   (e.g. testlookup-tfstate-<acct>)
#   TF_LOCK_TABLE     DynamoDB lock table   (default: testlookup-tflock)
###############################################################################
set -euo pipefail

ENVIRONMENT="${1:?usage: plan.sh <dev|staging|prod>}"
case "$ENVIRONMENT" in dev|staging|prod) ;; *) echo "env must be dev|staging|prod"; exit 2;; esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="$(cd "$SCRIPT_DIR/../terraform" && pwd)"
REGION="${AWS_REGION:-us-east-1}"
TF_STATE_BUCKET="${TF_STATE_BUCKET:?set TF_STATE_BUCKET (see bootstrap-state.sh)}"
TF_LOCK_TABLE="${TF_LOCK_TABLE:-testlookup-tflock}"

cd "$TF_DIR"
echo "[plan] env=$ENVIRONMENT region=$REGION bucket=$TF_STATE_BUCKET"

terraform init -input=false -reconfigure \
  -backend-config="bucket=$TF_STATE_BUCKET" \
  -backend-config="key=aws/$ENVIRONMENT/terraform.tfstate" \
  -backend-config="region=$REGION" \
  -backend-config="dynamodb_table=$TF_LOCK_TABLE" \
  -backend-config="encrypt=true"

# -lock=false keeps a plan from blocking on a stuck lock; plan never mutates infra.
terraform plan -input=false -lock=false \
  -var-file="environments/${ENVIRONMENT}.tfvars" \
  -out="${ENVIRONMENT}.plan"

echo "[plan] wrote ${ENVIRONMENT}.plan — review it, then: ./deploy.sh ${ENVIRONMENT}"
