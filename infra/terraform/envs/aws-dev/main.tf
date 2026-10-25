# AWS dev environment: composes the platform modules. Networking (VPC, subnets) comes from the
# existing landing zone and is passed in as variables. The ECR repository comes from
# envs/aws-bootstrap, applied before the image is pushed.
#
# private_networking = false (phase 1): AWS APIs and OpenSearch Serverless over public endpoints,
#   so tasks need NAT egress (or public subnets + assign_public_ip); the ingest Lambda runs outside
#   the VPC; the pipeline's hosted agent runs the cloud eval directly.
# private_networking = true (phase 2): VPC endpoints for every service, OpenSearch reachable only
#   through its VPC endpoint, Lambda in the VPC; the cloud eval runs as an ECS task in the VPC.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
  }
  backend "s3" {} # configured by pipelines/templates/terraform.yml (-backend-config)
}

provider "aws" {
  region = var.region
  default_tags { tags = local.tags }
}

variable "region" {
  type    = string
  default = "ap-south-1"
}
variable "env" {
  type    = string
  default = "dev"
}
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "private_networking" {
  type    = bool
  default = false
}
variable "private_route_table_ids" {
  description = "Route tables of private_subnet_ids; required when private_networking = true (S3 gateway endpoint)"
  type        = list(string)
  default     = []
}
variable "assign_public_ip" {
  description = "Phase 1 only, when private_subnet_ids are public subnets without NAT"
  type        = bool
  default     = false
}
variable "eval_principal_arns" {
  description = "IAM roles that run the cloud eval from outside the VPC (the pipeline's AWS service connection role)"
  type        = list(string)
  default     = []
}
variable "image_tag" { type = string }
variable "bedrock_chat_model" { type = string }
variable "bedrock_fallback_model" { type = string }
variable "alert_email" { type = string }

locals {
  name = "genai-${var.env}"
  tags = { project = "genai-platform", env = var.env, managed_by = "terraform" }
}

resource "aws_kms_key" "platform" {
  description         = "${local.name} data key"
  enable_key_rotation = true
}

resource "aws_security_group" "platform" {
  name   = "${local.name}-sg"
  vpc_id = var.vpc_id
  ingress {
    from_port   = 0
    to_port     = 65535
    protocol    = "tcp"
    self        = true
    description = "intra-platform (tasks, ALB, API Gateway VPC link, VPC endpoints)"
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

data "aws_ecr_repository" "platform" {
  name = local.name
}

locals {
  image_uri = "${data.aws_ecr_repository.platform.repository_url}:${var.image_tag}"
}

module "endpoints" {
  source             = "../../modules/aws/endpoints"
  count              = var.private_networking ? 1 : 0
  name               = local.name
  region             = var.region
  vpc_id             = var.vpc_id
  subnet_ids         = var.private_subnet_ids
  security_group_ids = [aws_security_group.platform.id]
  route_table_ids    = var.private_route_table_ids
  tags               = local.tags
}

module "service" {
  source             = "../../modules/aws/service"
  name               = local.name
  vpc_id             = var.vpc_id
  subnet_ids         = var.private_subnet_ids
  security_group_ids = [aws_security_group.platform.id]
  assign_public_ip   = var.assign_public_ip && !var.private_networking
  image_uri          = local.image_uri
  environment        = local.app_env
  tags               = local.tags
  depends_on         = [module.endpoints] # tasks pull the image through the ECR endpoints in phase 2
}

module "ingest" {
  source             = "../../modules/aws/ingest"
  name               = local.name
  kms_key_arn        = aws_kms_key.platform.arn
  image_uri          = local.image_uri
  in_vpc             = var.private_networking
  subnet_ids         = var.private_subnet_ids
  security_group_ids = [aws_security_group.platform.id]
  environment        = local.app_env
  tags               = local.tags
  depends_on         = [module.endpoints]
}

module "vector" {
  source                  = "../../modules/aws/opensearch_vector"
  name                    = "${local.name}-kb"
  kms_key_arn             = aws_kms_key.platform.arn
  private_access          = var.private_networking
  vpc_id                  = var.vpc_id
  subnet_ids              = var.private_subnet_ids
  security_group_ids      = [aws_security_group.platform.id]
  reader_writer_role_arns = concat([module.service.task_role_arn, module.ingest.worker_role_arn], var.eval_principal_arns)
  tags                    = local.tags
}

module "bedrock" {
  source = "../../modules/aws/bedrock"
  name   = local.name
  tags   = local.tags
}

module "api_gateway" {
  source             = "../../modules/aws/api_gateway"
  name               = local.name
  region             = var.region
  subnet_ids         = var.private_subnet_ids
  security_group_ids = [aws_security_group.platform.id]
  alb_listener_arn   = module.service.alb_listener_arn
  tags               = local.tags
}

locals {
  app_env = {
    GENAI_PROFILE             = "profiles/knowledge-assistant.aws.yaml"
    GENAI_INGEST_ON_START     = "false"
    AWS_REGION                = var.region
    BEDROCK_CHAT_MODEL        = var.bedrock_chat_model
    BEDROCK_FALLBACK_MODEL    = var.bedrock_fallback_model
    AOSS_ENDPOINT             = module.vector.collection_endpoint
    BEDROCK_GUARDRAIL_ID      = module.bedrock.guardrail_id
    BEDROCK_GUARDRAIL_VERSION = module.bedrock.guardrail_version
  }
}

resource "aws_budgets_budget" "genai" {
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = "2000"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  cost_filter {
    name   = "TagKeyValue"
    values = ["user:project$genai-platform"]
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}

output "api_url" { value = module.api_gateway.api_url }
output "api_alb" { value = module.service.alb_dns_name }
output "user_pool_id" { value = module.api_gateway.user_pool_id }
output "user_pool_client_id" { value = module.api_gateway.user_pool_client_id }
output "ecr_repository_url" { value = data.aws_ecr_repository.platform.repository_url }
output "landing_bucket" { value = module.ingest.landing_bucket }
output "aoss_endpoint" { value = module.vector.collection_endpoint }
output "guardrail_id" { value = module.bedrock.guardrail_id }
# Read by pipelines/templates/cloud-eval.yml
output "private_networking" { value = var.private_networking }
output "app_env" { value = local.app_env }
output "ecs_cluster" { value = module.service.cluster_name }
output "task_definition_arn" { value = module.service.task_definition_arn }
output "subnet_ids" { value = var.private_subnet_ids }
output "security_group_id" { value = aws_security_group.platform.id }
output "assign_public_ip" { value = var.assign_public_ip && !var.private_networking }
