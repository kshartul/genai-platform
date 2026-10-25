# ECS Fargate service running the answer/agent API and the MCP server as two containers,
# behind an internal ALB. modules/aws/api_gateway puts an authenticated HTTP API in front of it.

variable "name" { type = string }
variable "vpc_id" { type = string }
variable "subnet_ids" { type = list(string) }
variable "security_group_ids" { type = list(string) }
variable "image_uri" { type = string }
variable "environment" {
  type    = map(string)
  default = {}
}
variable "assign_public_ip" {
  description = "Only for public subnets without NAT; tasks stay unreachable from outside (SG has no public ingress)"
  type        = bool
  default     = false
}
variable "desired_count" {
  type    = number
  default = 2
}
variable "tags" {
  type    = map(string)
  default = {}
}

locals {
  env = [for k, v in var.environment : { name = k, value = v }]
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/genai/${var.name}"
  retention_in_days = 90
  tags              = var.tags
}

data "aws_iam_policy_document" "assume_ecs" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.name}-exec"
  assume_role_policy = data.aws_iam_policy_document.assume_ecs.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = data.aws_iam_policy_document.assume_ecs.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "task" {
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock:Converse", "bedrock:ConverseStream", "bedrock:ApplyGuardrail", "bedrock:Rerank"], Resource = "*" },
      { Effect = "Allow", Action = ["aoss:APIAccessAll"], Resource = "*" },
      { Effect = "Allow", Action = ["neptune-db:ReadDataViaQuery", "neptune-db:WriteDataViaQuery", "neptune-db:connect"], Resource = "*" },
      { Effect = "Allow", Action = ["comprehend:DetectPiiEntities"], Resource = "*" }
    ]
  })
}

resource "aws_ecs_cluster" "this" {
  name = var.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
  tags = var.tags
}

resource "aws_ecs_task_definition" "this" {
  family                   = var.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 2048
  memory                   = 4096
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  container_definitions = jsonencode([
    {
      name         = "api"
      image        = var.image_uri
      essential    = true
      command      = ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
      portMappings = [{ containerPort = 8000 }]
      environment  = local.env
      logConfiguration = {
        logDriver = "awslogs"
        options   = { awslogs-group = aws_cloudwatch_log_group.this.name, awslogs-region = data.aws_region.current.name, awslogs-stream-prefix = "api" }
      }
    },
    {
      name         = "mcp"
      image        = var.image_uri
      essential    = false
      command      = ["python", "-m", "services.mcp_server.server", "--http", "--port", "8001"]
      portMappings = [{ containerPort = 8001 }]
      environment  = local.env
      logConfiguration = {
        logDriver = "awslogs"
        options   = { awslogs-group = aws_cloudwatch_log_group.this.name, awslogs-region = data.aws_region.current.name, awslogs-stream-prefix = "mcp" }
      }
    }
  ])
  tags = var.tags
}

data "aws_region" "current" {}

resource "aws_lb" "this" {
  name               = substr(var.name, 0, 32)
  internal           = true
  load_balancer_type = "application"
  subnets            = var.subnet_ids
  security_groups    = var.security_group_ids
  tags               = var.tags
}

resource "aws_lb_target_group" "api" {
  name        = substr("${var.name}-api", 0, 32)
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"
  health_check { path = "/healthz" }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_ecs_service" "this" {
  name            = var.name
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.this.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = var.security_group_ids
    assign_public_ip = var.assign_public_ip
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  tags = var.tags
}

resource "aws_appautoscaling_target" "this" {
  max_capacity       = 10
  min_capacity       = var.desired_count
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.this.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "cpu" {
  name               = "${var.name}-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.this.resource_id
  scalable_dimension = aws_appautoscaling_target.this.scalable_dimension
  service_namespace  = aws_appautoscaling_target.this.service_namespace
  target_tracking_scaling_policy_configuration {
    target_value = 60
    predefined_metric_specification { predefined_metric_type = "ECSServiceAverageCPUUtilization" }
  }
}

output "task_role_arn" { value = aws_iam_role.task.arn }
output "alb_dns_name" { value = aws_lb.this.dns_name }
output "alb_listener_arn" { value = aws_lb_listener.http.arn }
output "cluster_name" { value = aws_ecs_cluster.this.name }
output "task_definition_arn" { value = aws_ecs_task_definition.this.arn }
