environment         = "prod"
region              = "us-east-1"
allowed_account_ids = [] # STRONGLY recommended to pin the prod account id here

vpc_cidr           = "10.42.0.0/16"
az_count           = 3
single_nat_gateway = false # one NAT per AZ for HA

cluster_version        = "1.30"
node_instance_types    = ["m5.xlarge"]
node_desired_size      = 4
node_min_size          = 3
node_max_size          = 10
endpoint_public_access = true
# Lock the API endpoint to your office/VPN CIDRs in prod:
# public_access_cidrs  = ["203.0.113.0/24"]

rds_instance_class    = "db.r6g.large"
rds_allocated_storage = 100
rds_multi_az          = true
docdb_instance_class  = "db.r6g.large"
docdb_instance_count  = 3
redis_node_type       = "cache.m6g.large"
redis_replicas        = 2
