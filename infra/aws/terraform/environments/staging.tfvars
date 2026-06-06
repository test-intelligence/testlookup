environment         = "staging"
region              = "us-east-1"
allowed_account_ids = []

vpc_cidr           = "10.41.0.0/16"
az_count           = 3
single_nat_gateway = true

cluster_version     = "1.30"
node_instance_types = ["t3.large"]
node_desired_size   = 3
node_min_size       = 2
node_max_size       = 6

rds_instance_class   = "db.t3.large"
rds_multi_az         = false
docdb_instance_count = 2
redis_replicas       = 1
