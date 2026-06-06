variable "name" {
  description = "Name prefix for network resources."
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
}

variable "az_count" {
  description = "Number of AZs to span."
  type        = number
}

variable "single_nat_gateway" {
  description = "One NAT gateway for all private subnets (cheaper) vs one per AZ."
  type        = bool
}

variable "tags" {
  description = "Common tags."
  type        = map(string)
  default     = {}
}
