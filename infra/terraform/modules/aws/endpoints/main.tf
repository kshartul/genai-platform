# VPC endpoints (PrivateLink) for every AWS service the platform calls, so tasks and the ingest
# Lambda run in private subnets with no NAT. Only created when private_networking = true.

variable "name" { type = string }
variable "region" { type = string }
variable "vpc_id" { type = string }
variable "subnet_ids" { type = list(string) }
variable "security_group_ids" { type = list(string) }
variable "route_table_ids" {
  description = "Route tables of the private subnets (S3 gateway endpoint; ECR image layers are served from S3)"
  type        = list(string)
}
variable "interface_services" {
  type = list(string)
  default = [
    "bedrock-runtime",       # Converse, InvokeModel (embeddings), ApplyGuardrail
    "bedrock-agent-runtime", # Rerank
    "ecr.api", "ecr.dkr",    # image pulls (ECS and Lambda)
    "logs",                  # awslogs driver
    "sts", "sqs",
    "comprehend", "textract", # PII detection, document parsing
  ]
}
variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_vpc_endpoint" "interface" {
  for_each            = toset(var.interface_services)
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.subnet_ids
  security_group_ids  = var.security_group_ids
  private_dns_enabled = true
  tags                = merge(var.tags, { Name = "${var.name}-${each.key}" })
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = var.route_table_ids
  tags              = merge(var.tags, { Name = "${var.name}-s3" })
  lifecycle {
    precondition {
      condition     = length(var.route_table_ids) > 0
      error_message = "private_route_table_ids is required when private_networking = true."
    }
  }
}
