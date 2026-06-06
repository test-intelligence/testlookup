output "cluster_name" {
  description = "EKS cluster name."
  value       = aws_eks_cluster.this.name
}

output "cluster_endpoint" {
  description = "EKS API server endpoint."
  value       = aws_eks_cluster.this.endpoint
}

output "cluster_certificate_authority_data" {
  description = "Base64 CA data for kubeconfig."
  value       = aws_eks_cluster.this.certificate_authority[0].data
}

output "cluster_oidc_issuer_url" {
  description = "OIDC issuer URL."
  value       = aws_eks_cluster.this.identity[0].oidc[0].issuer
}

output "oidc_provider_arn" {
  description = "IAM OIDC provider ARN (for IRSA trust policies)."
  value       = aws_iam_openid_connect_provider.this.arn
}

output "node_security_group_id" {
  description = "Cluster security group shared by managed nodes (source for data-tier ingress)."
  value       = aws_eks_cluster.this.vpc_config[0].cluster_security_group_id
}
