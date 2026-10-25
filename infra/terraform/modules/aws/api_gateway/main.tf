# Public front door for the internal ALB: HTTP API + Cognito JWT authorizer + VPC link.
# The integration overwrites X-User / X-Groups from the verified token and strips X-Scopes, so a
# caller cannot assert its own identity; services/api derives scopes from "scope:*" groups.

variable "name" { type = string }
variable "region" { type = string }
variable "subnet_ids" { type = list(string) }
variable "security_group_ids" { type = list(string) }
variable "alb_listener_arn" { type = string }
variable "cognito_groups" {
  description = "Document ACL groups (match `acl:` in the corpus) and scope:<tool scope> groups"
  type        = list(string)
  default     = ["hr", "engineering", "scope:kb.read", "scope:graph.read", "scope:dw.read", "scope:tickets.write", "scope:kb.admin"]
}
variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_cognito_user_pool" "this" {
  name                     = "${var.name}-users"
  deletion_protection      = "ACTIVE"
  auto_verified_attributes = ["email"]
  admin_create_user_config { allow_admin_create_user_only = true }
  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = true
  }
  tags = var.tags
}

resource "aws_cognito_user_pool_client" "api" {
  name                          = "${var.name}-api"
  user_pool_id                  = aws_cognito_user_pool.this.id
  generate_secret               = false
  explicit_auth_flows           = ["ALLOW_USER_PASSWORD_AUTH", "ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors = "ENABLED"
  access_token_validity         = 60
  id_token_validity             = 60
  token_validity_units {
    access_token = "minutes"
    id_token     = "minutes"
  }
}

resource "aws_cognito_user_group" "this" {
  for_each     = toset(var.cognito_groups)
  name         = each.key
  user_pool_id = aws_cognito_user_pool.this.id
}

resource "aws_apigatewayv2_vpc_link" "this" {
  name               = "${var.name}-vpclink"
  subnet_ids         = var.subnet_ids
  security_group_ids = var.security_group_ids # same SG as the ALB, whose self-ingress rule admits it
  tags               = var.tags
}

resource "aws_apigatewayv2_api" "this" {
  name          = "${var.name}-api"
  protocol_type = "HTTP"
  tags          = var.tags
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.this.id
  name             = "cognito"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  jwt_configuration {
    issuer   = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.this.id}"
    audience = [aws_cognito_user_pool_client.api.id] # matched against aud (ID token) or client_id (access token)
  }
}

resource "aws_apigatewayv2_integration" "api" {
  api_id             = aws_apigatewayv2_api.this.id
  integration_type   = "HTTP_PROXY"
  integration_method = "ANY"
  integration_uri    = var.alb_listener_arn
  connection_type    = "VPC_LINK"
  connection_id      = aws_apigatewayv2_vpc_link.this.id
  request_parameters = {
    "overwrite:header.X-User"   = "$context.authorizer.claims.sub"
    "overwrite:header.X-Groups" = "$context.authorizer.claims.cognito:groups"
    "remove:header.X-Scopes"    = "''"
  }
}

resource "aws_apigatewayv2_integration" "health" {
  api_id             = aws_apigatewayv2_api.this.id
  integration_type   = "HTTP_PROXY"
  integration_method = "GET"
  integration_uri    = var.alb_listener_arn
  connection_type    = "VPC_LINK"
  connection_id      = aws_apigatewayv2_vpc_link.this.id
}

resource "aws_apigatewayv2_route" "api" {
  api_id             = aws_apigatewayv2_api.this.id
  route_key          = "ANY /{proxy+}"
  target             = "integrations/${aws_apigatewayv2_integration.api.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "health" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "GET /healthz"
  target    = "integrations/${aws_apigatewayv2_integration.health.id}"
}

resource "aws_cloudwatch_log_group" "access" {
  name              = "/genai/${var.name}/apigw"
  retention_in_days = 90
  tags              = var.tags
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_burst_limit = 50
    throttling_rate_limit  = 20
  }
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.access.arn
    format = jsonencode({
      requestId = "$context.requestId", user = "$context.authorizer.claims.sub", route = "$context.routeKey",
      status    = "$context.status", latencyMs = "$context.responseLatency", error = "$context.integrationErrorMessage"
    })
  }
  tags = var.tags
}

output "api_url" { value = aws_apigatewayv2_api.this.api_endpoint }
output "user_pool_id" { value = aws_cognito_user_pool.this.id }
output "user_pool_client_id" { value = aws_cognito_user_pool_client.api.id }
