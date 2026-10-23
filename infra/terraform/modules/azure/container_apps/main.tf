# Container Apps: API + MCP server (internal ingress) and the queue-triggered ingest job,
# all running as one user-assigned managed identity with least-privilege role assignments.

variable "name" { type = string }
variable "location" { type = string }
variable "resource_group_name" { type = string }
variable "infrastructure_subnet_id" { type = string }
variable "image" { type = string }
variable "acr_id" { type = string }
variable "acr_login_server" { type = string }
variable "environment" { type = map(string) }
variable "openai_id" { type = string }
variable "content_safety_id" { type = string }
variable "search_id" { type = string }
variable "storage_account_id" { type = string }
variable "storage_account_name" { type = string }
variable "ingest_queue_name" { type = string }
variable "tags" {
  type    = map(string)
  default = {}
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = "${var.name}-logs"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "PerGB2018"
  retention_in_days   = 90
  tags                = var.tags
}

resource "azurerm_application_insights" "this" {
  name                = "${var.name}-appi"
  location            = var.location
  resource_group_name = var.resource_group_name
  workspace_id        = azurerm_log_analytics_workspace.this.id
  application_type    = "web"
  tags                = var.tags
}

resource "azurerm_user_assigned_identity" "app" {
  name                = "${var.name}-id"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

locals {
  roles = {
    aoai     = { scope = var.openai_id, role = "Cognitive Services OpenAI User" }
    safety   = { scope = var.content_safety_id, role = "Cognitive Services User" }
    search   = { scope = var.search_id, role = "Search Index Data Contributor" }
    blob     = { scope = var.storage_account_id, role = "Storage Blob Data Reader" }
    queue    = { scope = var.storage_account_id, role = "Storage Queue Data Message Processor" }
    acr_pull = { scope = var.acr_id, role = "AcrPull" }
  }
  env = merge(var.environment, {
    AZURE_CLIENT_ID                       = azurerm_user_assigned_identity.app.client_id
    APPLICATIONINSIGHTS_CONNECTION_STRING = azurerm_application_insights.this.connection_string
  })
}

resource "azurerm_role_assignment" "app" {
  for_each             = local.roles
  scope                = each.value.scope
  role_definition_name = each.value.role
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

resource "azurerm_container_app_environment" "this" {
  name                           = "${var.name}-cae"
  location                       = var.location
  resource_group_name            = var.resource_group_name
  log_analytics_workspace_id     = azurerm_log_analytics_workspace.this.id
  infrastructure_subnet_id       = var.infrastructure_subnet_id
  internal_load_balancer_enabled = true
  tags                           = var.tags
}

resource "azurerm_container_app" "api" {
  name                         = "${var.name}-api"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }
  registry {
    server   = var.acr_login_server
    identity = azurerm_user_assigned_identity.app.id
  }
  template {
    min_replicas = 2
    max_replicas = 10
    container {
      name    = "api"
      image   = var.image
      cpu     = 1
      memory  = "2Gi"
      command = ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
      dynamic "env" {
        for_each = local.env
        content {
          name  = env.key
          value = env.value
        }
      }
      liveness_probe {
        transport = "HTTP"
        port      = 8000
        path      = "/healthz"
      }
    }
    http_scale_rule {
      name                = "http"
      concurrent_requests = "50"
    }
  }
  ingress {
    external_enabled = false
    target_port      = 8000
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }
  tags = var.tags
}

resource "azurerm_container_app" "mcp" {
  name                         = "${var.name}-mcp"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }
  registry {
    server   = var.acr_login_server
    identity = azurerm_user_assigned_identity.app.id
  }
  template {
    min_replicas = 1
    max_replicas = 5
    container {
      name    = "mcp"
      image   = var.image
      cpu     = 0.5
      memory  = "1Gi"
      command = ["python", "-m", "services.mcp_server.server", "--http", "--port", "8001"]
      dynamic "env" {
        for_each = local.env
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }
  ingress {
    external_enabled = false
    target_port      = 8001
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }
  tags = var.tags
}

resource "azurerm_container_app_job" "ingest" {
  name                         = "${var.name}-ingest"
  location                     = var.location
  resource_group_name          = var.resource_group_name
  container_app_environment_id = azurerm_container_app_environment.this.id
  replica_timeout_in_seconds   = 1800
  replica_retry_limit          = 2
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }
  registry {
    server   = var.acr_login_server
    identity = azurerm_user_assigned_identity.app.id
  }
  event_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
    scale {
      min_executions = 0
      max_executions = 10
      rules {
        name             = "ingest-queue"
        custom_rule_type = "azure-queue"
        metadata = {
          accountName = var.storage_account_name
          queueName   = var.ingest_queue_name
          queueLength = "5"
        }
        # KEDA authenticates with the job's managed identity (workload identity) - no keys.
      }
    }
  }
  template {
    container {
      name    = "ingest"
      image   = var.image
      cpu     = 2
      memory  = "4Gi"
      command = ["python", "-m", "services.ingest_worker.azure_job"]
      dynamic "env" {
        for_each = merge(local.env, { AZURE_STORAGE_ACCOUNT = var.storage_account_name, AZURE_INGEST_QUEUE = var.ingest_queue_name })
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }
  tags = var.tags
}

output "api_fqdn" { value = azurerm_container_app.api.ingress[0].fqdn }
output "mcp_fqdn" { value = azurerm_container_app.mcp.ingress[0].fqdn }
output "identity_principal_id" { value = azurerm_user_assigned_identity.app.principal_id }
