provider "aws" {
  region = var.region

  # Guard-rail: never let a misconfigured profile apply into the wrong account.
  # Set to the target account id per environment (empty = skip the check).
  allowed_account_ids = var.allowed_account_ids

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
      Repo        = "anandtopu/testlookup"
    }
  }
}
