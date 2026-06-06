###############################################################################
# Global
###############################################################################

variable "project_name" {
  description = "Project slug, used as a prefix for all resource names."
  type        = string
  default     = "testlookup"
}

variable "environment" {
  description = "Environment name (dev | staging | prod). Drives naming + sizing."
  type        = string

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "allowed_account_ids" {
  description = "AWS account ids this config is allowed to apply into. Empty = no guard."
  type        = list(string)
  default     = []
}

###############################################################################
# Network
###############################################################################

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.40.0.0/16"
}

variable "az_count" {
  description = "Number of Availability Zones to spread subnets across."
  type        = number
  default     = 3

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 4
    error_message = "az_count must be between 2 and 4."
  }
}

variable "single_nat_gateway" {
  description = "Use a single NAT gateway (cheaper, dev) vs one per AZ (HA, prod)."
  type        = bool
  default     = true
}

###############################################################################
# EKS
###############################################################################

variable "cluster_version" {
  description = "EKS Kubernetes minor version."
  type        = string
  default     = "1.30"
}

variable "node_instance_types" {
  description = "EC2 instance types for the managed node group."
  type        = list(string)
  default     = ["t3.large"]
}

variable "node_desired_size" {
  description = "Desired worker node count."
  type        = number
  default     = 3
}

variable "node_min_size" {
  description = "Minimum worker node count."
  type        = number
  default     = 2
}

variable "node_max_size" {
  description = "Maximum worker node count."
  type        = number
  default     = 6
}

variable "endpoint_public_access" {
  description = "Expose the EKS API endpoint publicly (restrict via CIDRs)."
  type        = bool
  default     = true
}

variable "public_access_cidrs" {
  description = "CIDRs allowed to reach the public EKS API endpoint."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

###############################################################################
# Data services
###############################################################################

variable "ecr_repositories" {
  description = "Application images to host in ECR."
  type        = list(string)
  default     = ["testlookup/backend", "testlookup/frontend", "testlookup/mcp"]
}

variable "rds_instance_class" {
  description = "RDS PostgreSQL instance class."
  type        = string
  default     = "db.t3.medium"
}

variable "rds_allocated_storage" {
  description = "RDS allocated storage (GiB)."
  type        = number
  default     = 50
}

variable "rds_multi_az" {
  description = "Run RDS in Multi-AZ (prod)."
  type        = bool
  default     = false
}

variable "docdb_instance_class" {
  description = "DocumentDB (MongoDB-compatible) instance class."
  type        = string
  default     = "db.t3.medium"
}

variable "docdb_instance_count" {
  description = "Number of DocumentDB instances (1 = no HA)."
  type        = number
  default     = 1
}

variable "redis_node_type" {
  description = "ElastiCache Redis node type."
  type        = string
  default     = "cache.t3.micro"
}

variable "redis_replicas" {
  description = "Replica nodes per Redis shard (0 = single node)."
  type        = number
  default     = 1
}
