# Landing storage + Event Grid: BlobCreated events land on a storage queue that triggers the
# Container Apps ingest job (KEDA azure-queue scaler).

variable "name" { type = string }
variable "location" { type = string }
variable "resource_group_name" { type = string }
variable "tags" {
  type    = map(string)
  default = {}
}

resource "azurerm_storage_account" "landing" {
  name                            = substr(replace("${var.name}landing", "-", ""), 0, 24)
  location                        = var.location
  resource_group_name             = var.resource_group_name
  account_tier                    = "Standard"
  account_replication_type        = "ZRS"
  min_tls_version                 = "TLS1_2"
  shared_access_key_enabled       = false
  allow_nested_items_to_be_public = false
  blob_properties {
    versioning_enabled = true
  }
  tags = var.tags
}

resource "azurerm_storage_container" "documents" {
  name                  = "documents"
  storage_account_id    = azurerm_storage_account.landing.id
  container_access_type = "private"
}

resource "azurerm_storage_queue" "ingest" {
  name                 = "ingest-events"
  storage_account_name = azurerm_storage_account.landing.name
}

resource "azurerm_eventgrid_system_topic" "storage" {
  name                = "${var.name}-storage-events"
  location            = var.location
  resource_group_name = var.resource_group_name
  source_resource_id  = azurerm_storage_account.landing.id
  topic_type          = "Microsoft.Storage.StorageAccounts"
  identity { type = "SystemAssigned" }
  tags = var.tags
}

resource "azurerm_role_assignment" "eventgrid_queue_sender" {
  scope                = azurerm_storage_account.landing.id
  role_definition_name = "Storage Queue Data Message Sender"
  principal_id         = azurerm_eventgrid_system_topic.storage.identity[0].principal_id
}

resource "azurerm_eventgrid_system_topic_event_subscription" "blob_created" {
  name                = "blob-created-to-queue"
  system_topic        = azurerm_eventgrid_system_topic.storage.name
  resource_group_name = var.resource_group_name
  included_event_types = ["Microsoft.Storage.BlobCreated"]
  subject_filter {
    subject_begins_with = "/blobServices/default/containers/documents/"
  }
  delivery_identity { type = "SystemAssigned" }
  storage_queue_endpoint {
    storage_account_id = azurerm_storage_account.landing.id
    queue_name         = azurerm_storage_queue.ingest.name
  }
  depends_on = [azurerm_role_assignment.eventgrid_queue_sender]
}

output "storage_account_id" { value = azurerm_storage_account.landing.id }
output "storage_account_name" { value = azurerm_storage_account.landing.name }
output "queue_name" { value = azurerm_storage_queue.ingest.name }
