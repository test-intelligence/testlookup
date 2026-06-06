###############################################################################
# Root composition — wires the four building-block modules together.
#
#   network → vpc/subnets/nat            (foundational)
#   ecr     → image registries           (independent)
#   eks     → cluster + node group       (depends on network)
#   data    → rds/docdb/redis/s3/secret  (depends on network + eks node SG)
#
# Stateless app workloads (backend, frontend, mcp, celery workers) run on EKS
# via the existing k8s/overlays/aws-eks Kustomize overlay. ChromaDB and Ollama
# have no managed AWS equivalent and stay in-cluster (EBS-backed). Postgres,
# Mongo and Redis are externalised to managed services here.
###############################################################################

locals {
  name = "${var.project_name}-${var.environment}"

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

module "network" {
  source = "./modules/network"

  name               = local.name
  vpc_cidr           = var.vpc_cidr
  az_count           = var.az_count
  single_nat_gateway = var.single_nat_gateway
  tags               = local.tags
}

module "ecr" {
  source = "./modules/ecr"

  repositories = var.ecr_repositories
  tags         = local.tags
}

module "eks" {
  source = "./modules/eks"

  name               = local.name
  cluster_version    = var.cluster_version
  private_subnet_ids = module.network.private_subnet_ids
  public_subnet_ids  = module.network.public_subnet_ids

  node_instance_types    = var.node_instance_types
  node_desired_size      = var.node_desired_size
  node_min_size          = var.node_min_size
  node_max_size          = var.node_max_size
  endpoint_public_access = var.endpoint_public_access
  public_access_cidrs    = var.public_access_cidrs

  tags = local.tags
}

module "data" {
  source = "./modules/data"

  name                  = local.name
  vpc_id                = module.network.vpc_id
  private_subnet_ids    = module.network.private_subnet_ids
  app_security_group_id = module.eks.node_security_group_id

  rds_instance_class    = var.rds_instance_class
  rds_allocated_storage = var.rds_allocated_storage
  rds_multi_az          = var.rds_multi_az
  docdb_instance_class  = var.docdb_instance_class
  docdb_instance_count  = var.docdb_instance_count
  redis_node_type       = var.redis_node_type
  redis_replicas        = var.redis_replicas

  tags = local.tags
}
