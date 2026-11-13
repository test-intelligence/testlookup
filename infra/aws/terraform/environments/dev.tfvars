environment         = "dev"
region              = "us-east-1"
allowed_account_ids = []

vpc_cidr           = "10.40.0.0/16"
az_count           = 2
single_nat_gateway = true

cluster_version     = "1.30"
node_instance_types = ["t3.large"]
node_desired_size   = 2
node_min_size       = 2
node_max_size       = 4

# Required, no default: the CIDRs that may reach the EKS API endpoint (your
# office/VPN egress). Terraform asks for it when it is not set here.
# public_access_cidrs = ["203.0.113.0/24"]

rds_instance_class   = "db.t3.medium"
rds_multi_az         = false
docdb_instance_count = 1
redis_replicas       = 0
