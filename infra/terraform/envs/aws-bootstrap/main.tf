# Resources that must exist before the image is built: the ECR repository. Applied by the Build
# stage before `docker push`; envs/aws-<env> reads the repository with a data source.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
  }
  backend "s3" {} # key genai/aws-bootstrap-<env>.tfstate, set by pipelines/templates/docker-push.yml
}

provider "aws" {
  region = var.region
  default_tags { tags = { project = "genai-platform", env = var.env, managed_by = "terraform" } }
}

variable "region" {
  type    = string
  default = "ap-south-1"
}
variable "env" {
  type    = string
  default = "dev"
}

# One image for API, MCP server and ingest worker.
resource "aws_ecr_repository" "platform" {
  name                 = "genai-${var.env}"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "KMS" }
}

resource "aws_ecr_lifecycle_policy" "platform" {
  repository = aws_ecr_repository.platform.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep the last 50 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 50 }
      action       = { type = "expire" }
    }]
  })
}

output "ecr_repository_url" { value = aws_ecr_repository.platform.repository_url }
