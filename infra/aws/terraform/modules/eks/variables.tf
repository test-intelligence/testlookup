variable "name" {
  description = "Name prefix (also the cluster name)."
  type        = string
}

variable "cluster_version" {
  description = "EKS Kubernetes version."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnets for worker nodes."
  type        = list(string)
}

variable "public_subnet_ids" {
  description = "Public subnets (for internet-facing load balancers)."
  type        = list(string)
}

variable "node_instance_types" {
  description = "Instance types for the managed node group."
  type        = list(string)
}

variable "node_desired_size" {
  type = number
}

variable "node_min_size" {
  type = number
}

variable "node_max_size" {
  type = number
}

variable "endpoint_public_access" {
  type = bool
}

variable "public_access_cidrs" {
  type = list(string)
}

variable "enable_network_policy" {
  description = "Make the VPC CNI enforce Kubernetes NetworkPolicy (addon setting enableNetworkPolicy). When false, EKS stores the policies and applies none of them. Turn it on for any cluster that other workloads share: without it nothing isolates the data stores. The aws-eks overlay admits the load balancer to the frontend, backend and MCP pods; if you render your own manifests, admit the ALB subnets (output public_subnet_cidrs) to allow-frontend and allow-backend first, or the application stops answering through the ALB. Needs VPC CNI v1.14 or later."
  type        = bool
  default     = false
}

variable "require_imdsv2" {
  description = "Require nodes to use a launch template that enforces IMDSv2 and sets the metadata hop limit to 1, so pods off the host network cannot read the node role's credentials. This compatibility input must remain true. Applying this to an existing cluster without the launch template REPLACES the managed node group (every node is recreated). The AWS Load Balancer Controller, and any other controller that reads the instance metadata service, needs its own IRSA role and explicit region and vpcId settings."
  type        = bool
  default     = true

  validation {
    condition     = var.require_imdsv2
    error_message = "require_imdsv2 must be true; node metadata hardening cannot be disabled."
  }
}

variable "tags" {
  type    = map(string)
  default = {}
}
