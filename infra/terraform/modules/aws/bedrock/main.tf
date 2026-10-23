# Private Bedrock access + a managed guardrail (content filters, prompt attacks, PII, denied topics,
# contextual grounding). Guardrail id/version are exported to the AWS profile.

variable "name" { type = string }
variable "vpc_id" { type = string }
variable "subnet_ids" { type = list(string) }
variable "security_group_ids" { type = list(string) }
variable "region" { type = string }
variable "denied_topics" {
  type = list(object({ name = string, definition = string }))
  default = [
    { name = "individual-compensation", definition = "Questions about the salary or pay of a specific named employee." }
  ]
}
variable "grounding_threshold" {
  type    = number
  default = 0.75
}
variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_vpc_endpoint" "bedrock_runtime" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${var.region}.bedrock-runtime"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.subnet_ids
  security_group_ids  = var.security_group_ids
  private_dns_enabled = true
  tags                = var.tags
}

resource "aws_vpc_endpoint" "bedrock_agent_runtime" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${var.region}.bedrock-agent-runtime"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.subnet_ids
  security_group_ids  = var.security_group_ids
  private_dns_enabled = true
  tags                = var.tags
}

resource "aws_bedrock_guardrail" "this" {
  name                      = "${var.name}-guardrail"
  blocked_input_messaging   = "This request was blocked by policy."
  blocked_outputs_messaging = "The answer was withheld by policy."
  description               = "Platform default guardrail"

  content_policy_config {
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
    filters_config {
      type            = "HATE"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "VIOLENCE"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "MISCONDUCT"
      input_strength  = "MEDIUM"
      output_strength = "MEDIUM"
    }
  }

  sensitive_information_policy_config {
    pii_entities_config {
      type   = "EMAIL"
      action = "ANONYMIZE"
    }
    pii_entities_config {
      type   = "PHONE"
      action = "ANONYMIZE"
    }
    pii_entities_config {
      type   = "CREDIT_DEBIT_CARD_NUMBER"
      action = "BLOCK"
    }
  }

  topic_policy_config {
    dynamic "topics_config" {
      for_each = var.denied_topics
      content {
        name       = topics_config.value.name
        definition = topics_config.value.definition
        type       = "DENY"
        examples   = []
      }
    }
  }

  contextual_grounding_policy_config {
    filters_config {
      type      = "GROUNDING"
      threshold = var.grounding_threshold
    }
    filters_config {
      type      = "RELEVANCE"
      threshold = 0.5
    }
  }

  tags = var.tags
}

resource "aws_bedrock_guardrail_version" "v" {
  guardrail_arn = aws_bedrock_guardrail.this.guardrail_arn
  description   = "Released by pipeline"
}

output "guardrail_id" { value = aws_bedrock_guardrail.this.guardrail_id }
output "guardrail_version" { value = aws_bedrock_guardrail_version.v.version }
