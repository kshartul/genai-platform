# Azure OpenAI (chat + embedding deployments) and Azure AI Content Safety.
# Entra ID auth only (local keys disabled); reached over private endpoints from the platform subnet.

variable "name" { type = string }
variable "location" { type = string }
variable "resource_group_name" { type = string }
variable "private_endpoint_subnet_id" { type = string }
variable "chat_model" {
  type    = object({ name = string, version = string, capacity = number })
  default = { name = "gpt-4o", version = "2024-11-20", capacity = 50 }
}
variable "fallback_model" {
  type    = object({ name = string, version = string, capacity = number })
  default = { name = "gpt-4o-mini", version = "2024-07-18", capacity = 50 }
}
variable "embedding_model" {
  type    = object({ name = string, version = string, capacity = number })
  default = { name = "text-embedding-3-large", version = "1", capacity = 100 }
}
variable "tags" {
  type    = map(string)
  default = {}
}

resource "azurerm_cognitive_account" "openai" {
  name                          = "${var.name}-aoai"
  location                      = var.location
  resource_group_name           = var.resource_group_name
  kind                          = "OpenAI"
  sku_name                      = "S0"
  custom_subdomain_name         = "${var.name}-aoai"
  local_auth_enabled            = false
  public_network_access_enabled = false
  identity { type = "SystemAssigned" }
  tags = var.tags
}

resource "azurerm_cognitive_deployment" "chat" {
  name                 = "chat"
  cognitive_account_id = azurerm_cognitive_account.openai.id
  model {
    format  = "OpenAI"
    name    = var.chat_model.name
    version = var.chat_model.version
  }
  sku {
    name     = "GlobalStandard"
    capacity = var.chat_model.capacity
  }
}

resource "azurerm_cognitive_deployment" "fallback" {
  name                 = "chat-fallback"
  cognitive_account_id = azurerm_cognitive_account.openai.id
  model {
    format  = "OpenAI"
    name    = var.fallback_model.name
    version = var.fallback_model.version
  }
  sku {
    name     = "GlobalStandard"
    capacity = var.fallback_model.capacity
  }
}

resource "azurerm_cognitive_deployment" "embedding" {
  name                 = "embed"
  cognitive_account_id = azurerm_cognitive_account.openai.id
  model {
    format  = "OpenAI"
    name    = var.embedding_model.name
    version = var.embedding_model.version
  }
  sku {
    name     = "Standard"
    capacity = var.embedding_model.capacity
  }
}

resource "azurerm_cognitive_account" "content_safety" {
  name                          = "${var.name}-safety"
  location                      = var.location
  resource_group_name           = var.resource_group_name
  kind                          = "ContentSafety"
  sku_name                      = "S0"
  custom_subdomain_name         = "${var.name}-safety"
  local_auth_enabled            = false
  public_network_access_enabled = false
  tags                          = var.tags
}

resource "azurerm_private_endpoint" "openai" {
  name                = "${var.name}-aoai-pe"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.private_endpoint_subnet_id
  private_service_connection {
    name                           = "aoai"
    private_connection_resource_id = azurerm_cognitive_account.openai.id
    subresource_names              = ["account"]
    is_manual_connection           = false
  }
}

resource "azurerm_private_endpoint" "content_safety" {
  name                = "${var.name}-safety-pe"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.private_endpoint_subnet_id
  private_service_connection {
    name                           = "safety"
    private_connection_resource_id = azurerm_cognitive_account.content_safety.id
    subresource_names              = ["account"]
    is_manual_connection           = false
  }
}

output "openai_id" { value = azurerm_cognitive_account.openai.id }
output "openai_endpoint" { value = azurerm_cognitive_account.openai.endpoint }
output "content_safety_id" { value = azurerm_cognitive_account.content_safety.id }
output "content_safety_endpoint" { value = azurerm_cognitive_account.content_safety.endpoint }
output "chat_deployment" { value = azurerm_cognitive_deployment.chat.name }
output "fallback_deployment" { value = azurerm_cognitive_deployment.fallback.name }
output "embedding_deployment" { value = azurerm_cognitive_deployment.embedding.name }
