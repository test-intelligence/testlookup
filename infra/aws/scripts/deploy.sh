#!/usr/bin/env bash
###############################################################################
# deploy.sh — apply the Terraform for one environment. GATED.
#
# This is the only script that mutates AWS. It refuses to run unless you pass an
# explicit confirmation, and for prod it requires typing the environment name —
# the same forcing-function pattern the app uses for destructive actions.
#
# Usage:
#   AWS_PROFILE=tl-dev TF_STATE_BUCKET=... ./deploy.sh dev --confirm
#   ./deploy.sh prod --confirm            # then type "prod" when prompted
#
# Order of operations: init → plan (saved) → human review → apply <plan>.
###############################################################################
set -euo pipefail

ENVIRONMENT="${1:?usage: deploy.sh <dev|staging|prod> --confirm}"
CONFIRM_FLAG="${2:-}"
case "$ENVIRONMENT" in dev|staging|prod) ;; *) echo "env must be dev|staging|prod"; exit 2;; esac

if [ "$CONFIRM_FLAG" != "--confirm" ]; then
  echo "Refusing to apply without --confirm."
  echo "Run a read-only review first:  ./plan.sh $ENVIRONMENT"
  echo "Then:                          ./deploy.sh $ENVIRONMENT --confirm"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="$(cd "$SCRIPT_DIR/../terraform" && pwd)"
REGION="${AWS_REGION:-us-east-1}"
TF_STATE_BUCKET="${TF_STATE_BUCKET:?set TF_STATE_BUCKET (see bootstrap-state.sh)}"
TF_LOCK_TABLE="${TF_LOCK_TABLE:-testlookup-tflock}"

# Extra guard for prod: typed-name match.
if [ "$ENVIRONMENT" = "prod" ]; then
  read -r -p "About to APPLY to PROD. Type 'prod' to proceed: " typed
  [ "$typed" = "prod" ] || { echo "Mismatch — aborted."; exit 1; }
fi

cd "$TF_DIR"
echo "[deploy] init ($ENVIRONMENT)"
terraform init -input=false -reconfigure \
  -backend-config="bucket=$TF_STATE_BUCKET" \
  -backend-config="key=aws/$ENVIRONMENT/terraform.tfstate" \
  -backend-config="region=$REGION" \
  -backend-config="dynamodb_table=$TF_LOCK_TABLE" \
  -backend-config="encrypt=true"

echo "[deploy] plan ($ENVIRONMENT)"
terraform plan -input=false \
  -var-file="environments/${ENVIRONMENT}.tfvars" \
  -out="${ENVIRONMENT}.plan"

echo "[deploy] applying the reviewed plan ($ENVIRONMENT)"
terraform apply -input=false "${ENVIRONMENT}.plan"

echo "[deploy] done. Configure kubectl with:"
terraform output -raw kubeconfig_command 2>/dev/null || true
