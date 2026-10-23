# Azure dev environment: same platform, Azure services. VNet/subnets come from the landing zone.

terraform {
  required_version = ">= 1.6"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.10"
    }
  }
  backend "azurerm" {} # configured by pipelines/templates/terraform.yml (-backend-config)
}

provider "azurerm" {
  features {}
  storage_use_azuread = true
}

variable "location" {
  type    = string
  default = "centralindia"
}
variable "env" {
  type    = string
  default = "dev"
}
variable "resource_group_name" { type = string }
variable "apps_subnet_id" { type = string }
variable "private_endpoint_subnet_id" { type = string }
variable "acr_id" { type = string }
variable "acr_login_server" { type = string }
variable "image_tag" { type = string }

locals {
  name = "genai-${var.env}"
  tags = { project = "genai-platform", env = var.env, managed_by = "terraform" }
}

module "openai" {
  source                     = "../../modules/azure/openai"
  name                       = local.name
  location                   = var.location
  resource_group_name        = var.resource_group_name
  private_endpoint_subnet_id = var.private_endpoint_subnet_id
  tags                       = local.tags
}

module "search" {
  source                     = "../../modules/azure/ai_search"
  name                       = local.name
  location                   = var.location
  resource_group_name        = var.resource_group_name
  private_endpoint_subnet_id = var.private_endpoint_subnet_id
  tags                       = local.tags
}

module "ingest" {
  source              = "../../modules/azure/ingest"
  name                = local.name
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = local.tags
}

module "apps" {
  source                   = "../../modules/azure/container_apps"
  name                     = local.name
  location                 = var.location
  resource_group_name      = var.resource_group_name
  infrastructure_subnet_id = var.apps_subnet_id
  image                    = "${var.acr_login_server}/genai-platform:${var.image_tag}"
  acr_id                   = var.acr_id
  acr_login_server         = var.acr_login_server
  openai_id                = module.openai.openai_id
  content_safety_id        = module.openai.content_safety_id
  search_id                = module.search.id
  storage_account_id       = module.ingest.storage_account_id
  storage_account_name     = module.ingest.storage_account_name
  ingest_queue_name        = module.ingest.queue_name
  environment = {
    GENAI_PROFILE                    = "profiles/knowledge-assistant.azure.yaml"
    GENAI_INGEST_ON_START            = "false"
    AZURE_OPENAI_ENDPOINT            = module.openai.openai_endpoint
    AZURE_OPENAI_CHAT_DEPLOYMENT     = module.openai.chat_deployment
    AZURE_OPENAI_FALLBACK_DEPLOYMENT = module.openai.fallback_deployment
    AZURE_OPENAI_EMBED_DEPLOYMENT    = module.openai.embedding_deployment
    AZURE_SEARCH_ENDPOINT            = module.search.endpoint
    AZURE_CONTENT_SAFETY_ENDPOINT    = module.openai.content_safety_endpoint
  }
  tags = local.tags
}

output "api_fqdn" { value = module.apps.api_fqdn }
output "mcp_fqdn" { value = module.apps.mcp_fqdn }
output "search_endpoint" { value = module.search.endpoint }
output "openai_endpoint" { value = module.openai.openai_endpoint }
