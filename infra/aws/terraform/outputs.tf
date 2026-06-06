output "region" {
  description = "AWS region."
  value       = var.region
}

output "cluster_name" {
  description = "EKS cluster name."
  value       = module.eks.cluster_name
}

output "cluster_endpoint" {
  description = "EKS API server endpoint."
  value       = module.eks.cluster_endpoint
}

output "cluster_oidc_provider_arn" {
  description = "IAM OIDC provider ARN for IRSA."
  value       = module.eks.oidc_provider_arn
}

output "ecr_repository_urls" {
  description = "Map of image name → ECR repository URL."
  value       = module.ecr.repository_urls
}

output "rds_endpoint" {
  description = "RDS PostgreSQL endpoint (host:port)."
  value       = module.data.rds_endpoint
}

output "docdb_endpoint" {
  description = "DocumentDB cluster endpoint."
  value       = module.data.docdb_endpoint
}

output "redis_primary_endpoint" {
  description = "ElastiCache Redis primary endpoint."
  value       = module.data.redis_primary_endpoint
}

output "s3_buckets" {
  description = "Application S3 bucket names (MinIO replacement)."
  value       = module.data.s3_bucket_names
}

output "app_secret_arn" {
  description = "Secrets Manager ARN holding the assembled testlookup-secrets payload."
  value       = module.data.app_secret_arn
}

output "kubeconfig_command" {
  description = "Run this to configure kubectl against the new cluster."
  value       = "aws eks update-kubeconfig --region ${var.region} --name ${module.eks.cluster_name}"
}
