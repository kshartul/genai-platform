# OpenSearch Serverless VECTORSEARCH collection: k-NN + BM25 for hybrid retrieval, encrypted with a
# customer-managed KMS key. private_access = false: public endpoint, IAM data-access policy only
# (phase 1). private_access = true: reachable only through the collection's VPC endpoint.

variable "name" { type = string }
variable "kms_key_arn" { type = string }
variable "vpc_id" { type = string }
variable "subnet_ids" { type = list(string) }
variable "security_group_ids" { type = list(string) }
variable "private_access" {
  type    = bool
  default = true
}
variable "reader_writer_role_arns" {
  description = "IAM roles (API, ingest worker, pipeline eval role) allowed to read/write indexes"
  type        = list(string)
}
variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_opensearchserverless_security_policy" "encryption" {
  name = "${var.name}-enc"
  type = "encryption"
  policy = jsonencode({
    Rules       = [{ ResourceType = "collection", Resource = ["collection/${var.name}"] }]
    AWSOwnedKey = false
    KmsARN      = var.kms_key_arn
  })
}

resource "aws_opensearchserverless_vpc_endpoint" "this" {
  count              = var.private_access ? 1 : 0
  name               = "${var.name}-vpce"
  vpc_id             = var.vpc_id
  subnet_ids         = var.subnet_ids
  security_group_ids = var.security_group_ids
}

locals {
  network_rules = [
    { ResourceType = "collection", Resource = ["collection/${var.name}"] },
    { ResourceType = "dashboard", Resource = ["collection/${var.name}"] }
  ]
}

resource "aws_opensearchserverless_security_policy" "network" {
  name = "${var.name}-net"
  type = "network"
  policy = (var.private_access
    ? jsonencode([{ Rules = local.network_rules, AllowFromPublic = false, SourceVPCEs = [aws_opensearchserverless_vpc_endpoint.this[0].id] }])
  : jsonencode([{ Rules = local.network_rules, AllowFromPublic = true }]))
}

resource "aws_opensearchserverless_access_policy" "data" {
  name = "${var.name}-data"
  type = "data"
  policy = jsonencode([{
    Rules = [
      {
        ResourceType = "index"
        Resource     = ["index/${var.name}/*"]
        Permission   = ["aoss:CreateIndex", "aoss:DescribeIndex", "aoss:ReadDocument", "aoss:WriteDocument", "aoss:UpdateIndex"]
      },
      {
        ResourceType = "collection"
        Resource     = ["collection/${var.name}"]
        Permission   = ["aoss:DescribeCollectionItems"]
      }
    ]
    Principal = var.reader_writer_role_arns
  }])
}

resource "aws_opensearchserverless_collection" "this" {
  name             = var.name
  type             = "VECTORSEARCH"
  standby_replicas = "ENABLED"
  tags             = var.tags
  depends_on       = [aws_opensearchserverless_security_policy.encryption, aws_opensearchserverless_security_policy.network]
}

output "collection_endpoint" { value = aws_opensearchserverless_collection.this.collection_endpoint }
output "collection_arn" { value = aws_opensearchserverless_collection.this.arn }
