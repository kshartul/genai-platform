# AWS dev environment: composes the platform modules. Networking (VPC, private subnets) comes
# from the existing landing zone and is passed in as variables.

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
    description = "intra-platform"
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# One image for API, MCP server and ingest worker; pushed by the pipeline before apply.
resource "aws_ecr_repository" "platform" {
  name                 = local.name
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
}

locals {
  image_uri = "${aws_ecr_repository.platform.repository_url}:${var.image_tag}"
}

module "service" {
  source             = "../../modules/aws/service"
  name               = local.name
  vpc_id             = var.vpc_id
  subnet_ids         = var.private_subnet_ids
  security_group_ids = [aws_security_group.platform.id]
  image_uri          = local.image_uri
  environment        = local.app_env
  tags               = local.tags
}

module "ingest" {
  source             = "../../modules/aws/ingest"
  name               = local.name
  kms_key_arn        = aws_kms_key.platform.arn
  image_uri          = local.image_uri
  subnet_ids         = var.private_subnet_ids
  security_group_ids = [aws_security_group.platform.id]
  environment        = local.app_env
  tags               = local.tags
}

module "vector" {
  source                  = "../../modules/aws/opensearch_vector"
  name                    = "${local.name}-kb"
  kms_key_arn             = aws_kms_key.platform.arn
  vpc_id                  = var.vpc_id
  subnet_ids              = var.private_subnet_ids
  security_group_ids      = [aws_security_group.platform.id]
  reader_writer_role_arns = [module.service.task_role_arn, module.ingest.worker_role_arn]
  tags                    = local.tags
}

module "bedrock" {
  source             = "../../modules/aws/bedrock"
  name               = local.name
  region             = var.region
  vpc_id             = var.vpc_id
  subnet_ids         = var.private_subnet_ids
  security_group_ids = [aws_security_group.platform.id]
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

output "api_alb" { value = module.service.alb_dns_name }
output "ecr_repository_url" { value = aws_ecr_repository.platform.repository_url }
output "landing_bucket" { value = module.ingest.landing_bucket }
output "aoss_endpoint" { value = module.vector.collection_endpoint }
output "guardrail_id" { value = module.bedrock.guardrail_id }
