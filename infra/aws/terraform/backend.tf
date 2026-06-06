# Remote state backend.
#
# Values are intentionally omitted here so the same root module can target any
# environment via `-backend-config` (see scripts/bootstrap-state.sh, which
# creates the S3 bucket + DynamoDB lock table). For local quality checks we run
# `terraform init -backend=false` so no AWS account or state bucket is touched.
#
# Example init:
#   terraform init \
#     -backend-config="bucket=testlookup-tfstate-<acct-id>" \
#     -backend-config="key=aws/dev/terraform.tfstate" \
#     -backend-config="region=us-east-1" \
#     -backend-config="dynamodb_table=testlookup-tflock" \
#     -backend-config="encrypt=true"
terraform {
  backend "s3" {}
}
