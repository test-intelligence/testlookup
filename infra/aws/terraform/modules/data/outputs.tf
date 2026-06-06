output "rds_endpoint" {
  description = "RDS PostgreSQL endpoint."
  value       = aws_db_instance.postgres.endpoint
}

output "docdb_endpoint" {
  description = "DocumentDB cluster endpoint."
  value       = aws_docdb_cluster.this.endpoint
}

output "redis_primary_endpoint" {
  description = "Redis primary endpoint."
  value       = aws_elasticache_replication_group.this.primary_endpoint_address
}

output "s3_bucket_names" {
  description = "Map of logical name → bucket name."
  value       = { for k, b in aws_s3_bucket.this : k => b.bucket }
}

output "app_secret_arn" {
  description = "Secrets Manager ARN for the assembled app secret."
  value       = aws_secretsmanager_secret.app.arn
}

output "data_security_group_id" {
  description = "Data-tier security group id."
  value       = aws_security_group.data.id
}
