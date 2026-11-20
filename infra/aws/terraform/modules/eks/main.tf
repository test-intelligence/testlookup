###############################################################################
# Cluster IAM role
###############################################################################
data "aws_iam_policy_document" "cluster_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "cluster" {
  name               = "${var.name}-eks-cluster"
  assume_role_policy = data.aws_iam_policy_document.cluster_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "cluster_policy" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

###############################################################################
# Control plane
###############################################################################
resource "aws_eks_cluster" "this" {
  name     = var.name
  version  = var.cluster_version
  role_arn = aws_iam_role.cluster.arn

  vpc_config {
    subnet_ids              = concat(var.private_subnet_ids, var.public_subnet_ids)
    endpoint_private_access = true
    endpoint_public_access  = var.endpoint_public_access
    public_access_cidrs     = var.public_access_cidrs
  }

  # Send control-plane logs to CloudWatch for auditability.
  enabled_cluster_log_types = ["api", "audit", "authenticator"]

  tags = var.tags

  depends_on = [aws_iam_role_policy_attachment.cluster_policy]
}

###############################################################################
# IRSA — OIDC provider so pods can assume IAM roles (ALB controller, EBS CSI…)
###############################################################################
data "tls_certificate" "oidc" {
  url = aws_eks_cluster.this.identity[0].oidc[0].issuer
}

resource "aws_iam_openid_connect_provider" "this" {
  url             = aws_eks_cluster.this.identity[0].oidc[0].issuer
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.oidc.certificates[0].sha1_fingerprint]
  tags            = var.tags
}

###############################################################################
# Node group IAM role
###############################################################################
data "aws_iam_policy_document" "node_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node" {
  name               = "${var.name}-eks-node"
  assume_role_policy = data.aws_iam_policy_document.node_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "node" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
  ])
  role       = aws_iam_role.node.name
  policy_arn = each.value
}

###############################################################################
# Node launch template: instance metadata settings only
###############################################################################
# IMDSv2 only, and a hop limit of 1: a response from the instance metadata
# service cannot cross the extra network hop into a pod, so a pod that is not
# on the host network cannot ask for the node role's credentials. The kubelet
# and the VPC CNI run on the node itself and are not affected. Controllers that
# read the region or VPC id from the metadata service (the AWS Load Balancer
# Controller does by default) must be given them as settings instead.
#
# Nothing else is set here on purpose. The instance types, the AMI, the
# bootstrap user data and the disk stay with the managed node group below, as
# they were before this template existed.
#
# Opt-in (var.require_imdsv2, default false). A node group that was created
# without a launch template cannot adopt one, so turning this on for an
# existing cluster replaces the node group. With the variable false this
# resource does not exist and the node group is exactly as it was.
resource "aws_launch_template" "node" {
  count = var.require_imdsv2 ? 1 : 0

  name_prefix = "${var.name}-ng-"

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  tags = var.tags

  lifecycle {
    create_before_destroy = true
  }
}

###############################################################################
# Managed node group (private subnets only)
###############################################################################
resource "aws_eks_node_group" "this" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "${var.name}-ng"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = var.private_subnet_ids
  instance_types  = var.node_instance_types

  dynamic "launch_template" {
    for_each = aws_launch_template.node
    content {
      id      = launch_template.value.id
      version = launch_template.value.latest_version
    }
  }

  scaling_config {
    desired_size = var.node_desired_size
    min_size     = var.node_min_size
    max_size     = var.node_max_size
  }

  update_config {
    max_unavailable = 1
  }

  tags = var.tags

  depends_on = [aws_iam_role_policy_attachment.node]

  lifecycle {
    ignore_changes = [scaling_config[0].desired_size]
  }
}

###############################################################################
# Core add-ons
###############################################################################
locals {
  # Settings for one addon, keyed by addon name; an addon that is not listed
  # keeps its defaults (configuration_values stays unset).
  #
  # vpc-cni: the AWS VPC CNI accepts NetworkPolicy objects and enforces none of
  # them until enableNetworkPolicy is "true" (a string in the addon schema).
  # Every policy in k8s/base/networkpolicy.yaml depends on this, default-deny
  # included. It needs VPC CNI v1.14 or later.
  #
  # Opt-in (var.enable_network_policy, default false): once it is on,
  # default-deny-all really applies, and a load balancer that sends straight to
  # pod IPs is refused until allow-frontend and allow-backend admit it.
  addon_configuration_values = var.enable_network_policy ? {
    "vpc-cni" = jsonencode({ enableNetworkPolicy = "true" })
  } : {}
}

resource "aws_eks_addon" "this" {
  for_each = toset(["vpc-cni", "coredns", "kube-proxy", "aws-ebs-csi-driver"])

  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = each.value
  configuration_values        = lookup(local.addon_configuration_values, each.value, null)
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"

  tags = var.tags

  depends_on = [aws_eks_node_group.this]
}
