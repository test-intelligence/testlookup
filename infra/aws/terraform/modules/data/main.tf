###############################################################################
# Credentials (generated; never hard-coded). Stored in Secrets Manager below.
###############################################################################
resource "random_password" "rds" {
  length  = 24
  special = false
}

resource "random_password" "docdb" {
  length  = 24
  special = false
}

resource "random_password" "redis" {
  length  = 32
  special = false
}

resource "random_password" "app_secret" {
  length  = 48
  special = false
}

resource "random_password" "jwt_secret" {
  length  = 48
  special = false
}

resource "random_password" "webhook_secret" {
  length  = 48
  special = false
}

resource "random_id" "bucket_suffix" {
  byte_length = 4
}

###############################################################################
# Data-tier security group — only the app SG may reach the databases.
###############################################################################
resource "aws_security_group" "data" {
  name        = "${var.name}-data"
  description = "Postgres/Mongo/Redis access from EKS workloads only"
  vpc_id      = var.vpc_id
  tags        = merge(var.tags, { Name = "${var.name}-data" })
}

locals {
  data_ports = {
    postgres = 5432
    docdb    = 27017
    redis    = 6379
  }
}

resource "aws_security_group_rule" "ingress" {
  for_each = local.data_ports

  type                     = "ingress"
  description              = "${each.key} from app"
  from_port                = each.value
  to_port                  = each.value
  protocol                 = "tcp"
  security_group_id        = aws_security_group.data.id
  source_security_group_id = var.app_security_group_id
}

resource "aws_security_group_rule" "egress" {
  type              = "egress"
  description       = "all outbound"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  security_group_id = aws_security_group.data.id
  cidr_blocks       = ["0.0.0.0/0"]
}

###############################################################################
# RDS PostgreSQL
###############################################################################
resource "aws_db_subnet_group" "this" {
  name       = "${var.name}-pg"
  subnet_ids = var.private_subnet_ids
  tags       = var.tags
}

resource "aws_db_instance" "postgres" {
  identifier     = "${var.name}-pg"
  engine         = "postgres"
  engine_version = "16.4"
  instance_class = var.rds_instance_class

  allocated_storage     = var.rds_allocated_storage
  max_allocated_storage = var.rds_allocated_storage * 4
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "testlookup"
  username = "testlookup_user"
  password = random_password.rds.result
  port     = 5432

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.data.id]
  multi_az               = var.rds_multi_az

  backup_retention_period    = 7
  deletion_protection        = true
  skip_final_snapshot        = false
  final_snapshot_identifier  = "${var.name}-pg-final"
  auto_minor_version_upgrade = true

  tags = var.tags
}

###############################################################################
# DocumentDB (MongoDB-compatible)
###############################################################################
resource "aws_docdb_subnet_group" "this" {
  name       = "${var.name}-docdb"
  subnet_ids = var.private_subnet_ids
  tags       = var.tags
}

resource "aws_docdb_cluster" "this" {
  cluster_identifier        = "${var.name}-docdb"
  engine                    = "docdb"
  master_username           = "testlookup_admin"
  master_password           = random_password.docdb.result
  port                      = 27017
  db_subnet_group_name      = aws_docdb_subnet_group.this.name
  vpc_security_group_ids    = [aws_security_group.data.id]
  storage_encrypted         = true
  backup_retention_period   = 7
  deletion_protection       = true
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.name}-docdb-final"

  tags = var.tags
}

resource "aws_docdb_cluster_instance" "this" {
  count              = var.docdb_instance_count
  identifier         = "${var.name}-docdb-${count.index}"
  cluster_identifier = aws_docdb_cluster.this.id
  instance_class     = var.docdb_instance_class
  tags               = var.tags
}

###############################################################################
# ElastiCache Redis
###############################################################################
resource "aws_elasticache_subnet_group" "this" {
  name       = "${var.name}-redis"
  subnet_ids = var.private_subnet_ids
  tags       = var.tags
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id = "${var.name}-redis"
  description          = "TestLookup Redis (Streams + Celery broker + cache)"
  engine               = "redis"
  engine_version       = "7.1"
  node_type            = var.redis_node_type
  port                 = 6379

  num_node_groups         = 1
  replicas_per_node_group = var.redis_replicas

  automatic_failover_enabled = var.redis_replicas > 0
  multi_az_enabled           = var.redis_replicas > 0

  subnet_group_name  = aws_elasticache_subnet_group.this.name
  security_group_ids = [aws_security_group.data.id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token                 = random_password.redis.result

  snapshot_retention_limit = 5

  tags = var.tags
}

###############################################################################
# S3 buckets (replace MinIO: raw telemetry + knowledge docs)
###############################################################################
locals {
  buckets = ["telemetry", "knowledge-docs"]
}

resource "aws_s3_bucket" "this" {
  for_each = toset(local.buckets)
  bucket   = "${var.name}-${each.value}-${random_id.bucket_suffix.hex}"
  tags     = var.tags
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = aws_s3_bucket.this
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

###############################################################################
# Secrets Manager — assembled "testlookup-secrets" payload the app consumes.
# Mirror these keys into the k8s `testlookup-secrets` Secret via External
# Secrets Operator (see infra/aws/STRATEGY.md §Secrets).
###############################################################################
resource "aws_secretsmanager_secret" "app" {
  name        = "${var.name}/app"
  description = "Connection strings + app secrets for TestLookup"
  tags        = var.tags
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    DATABASE_URL = "postgresql+asyncpg://testlookup_user:${random_password.rds.result}@${aws_db_instance.postgres.address}:5432/testlookup"
    MONGO_URI    = "mongodb://testlookup_admin:${random_password.docdb.result}@${aws_docdb_cluster.this.endpoint}:27017/?tls=true&retryWrites=false"
    REDIS_URL    = "rediss://:${random_password.redis.result}@${aws_elasticache_replication_group.this.primary_endpoint_address}:6379/0"

    APP_SECRET_KEY = random_password.app_secret.result
    JWT_SECRET_KEY = random_password.jwt_secret.result
    WEBHOOK_SECRET = random_password.webhook_secret.result

    STORAGE_BACKEND     = "s3"
    S3_TELEMETRY_BUCKET = aws_s3_bucket.this["telemetry"].bucket
    S3_KNOWLEDGE_BUCKET = aws_s3_bucket.this["knowledge-docs"].bucket
  })
}
