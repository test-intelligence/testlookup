variable "name" {
  description = "Name prefix."
  type        = string
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "app_security_group_id" {
  description = "Security group of the workloads allowed to reach the data tier (EKS nodes)."
  type        = string
}

variable "rds_instance_class" {
  type = string
}

variable "rds_allocated_storage" {
  type = number
}

variable "rds_multi_az" {
  type = bool
}

variable "docdb_instance_class" {
  type = string
}

variable "docdb_instance_count" {
  type = number
}

variable "redis_node_type" {
  type = string
}

variable "redis_replicas" {
  type = number
}

variable "tags" {
  type    = map(string)
  default = {}
}
