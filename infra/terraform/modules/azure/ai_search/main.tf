# Azure AI Search with semantic ranker; the vector index schema is applied by
# scripts/create_azure_index.py in the pipeline (index schemas evolve with embedding models).

variable "name" { type = string }
variable "location" { type = string }
variable "resource_group_name" { type = string }
variable "private_endpoint_subnet_id" { type = string }
variable "sku" {
  type    = string
  default = "standard"
}
variable "replica_count" {
  type    = number
  default = 2
}
variable "tags" {
  type    = map(string)
  default = {}
}

resource "azurerm_search_service" "this" {
  name                          = "${var.name}-search"
  location                      = var.location
  resource_group_name           = var.resource_group_name
  sku                           = var.sku
  replica_count                 = var.replica_count
  partition_count               = 1
  semantic_search_sku           = "standard"
  local_authentication_enabled  = false
  public_network_access_enabled = false
  identity { type = "SystemAssigned" }
  tags = var.tags
}

resource "azurerm_private_endpoint" "search" {
  name                = "${var.name}-search-pe"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.private_endpoint_subnet_id
  private_service_connection {
    name                           = "search"
    private_connection_resource_id = azurerm_search_service.this.id
    subresource_names              = ["searchService"]
    is_manual_connection           = false
  }
}

output "id" { value = azurerm_search_service.this.id }
output "endpoint" { value = "https://${azurerm_search_service.this.name}.search.windows.net" }
