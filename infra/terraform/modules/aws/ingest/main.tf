# Event-driven ingestion: S3 (landing) -> EventBridge -> SQS (+DLQ) -> Lambda (container image)
# running services/ingest_worker/handlers.aws_handler.

variable "name" { type = string }
variable "kms_key_arn" { type = string }
variable "image_uri" { type = string }
variable "subnet_ids" { type = list(string) }
variable "security_group_ids" { type = list(string) }
variable "environment" {
  type    = map(string)
  default = {}
}
variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_s3_bucket" "landing" {
  bucket = "${var.name}-landing"
  tags   = var.tags
}

resource "aws_s3_bucket_public_access_block" "landing" {
  bucket                  = aws_s3_bucket.landing.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "landing" {
  bucket = aws_s3_bucket.landing.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "landing" {
  bucket = aws_s3_bucket.landing.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
  }
}

resource "aws_s3_bucket_notification" "eventbridge" {
  bucket      = aws_s3_bucket.landing.id
  eventbridge = true
}

resource "aws_sqs_queue" "dlq" {
  name                      = "${var.name}-ingest-dlq"
  kms_master_key_id         = var.kms_key_arn
  message_retention_seconds = 1209600
  tags                      = var.tags
}

resource "aws_sqs_queue" "ingest" {
  name                       = "${var.name}-ingest"
  kms_master_key_id          = var.kms_key_arn
  visibility_timeout_seconds = 960
  redrive_policy             = jsonencode({ deadLetterTargetArn = aws_sqs_queue.dlq.arn, maxReceiveCount = 3 })
  tags                       = var.tags
}

resource "aws_cloudwatch_event_rule" "object_created" {
  name = "${var.name}-object-created"
  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail        = { bucket = { name = [aws_s3_bucket.landing.bucket] } }
  })
}

resource "aws_cloudwatch_event_target" "to_sqs" {
  rule = aws_cloudwatch_event_rule.object_created.name
  arn  = aws_sqs_queue.ingest.arn
}

resource "aws_sqs_queue_policy" "allow_eventbridge" {
  queue_url = aws_sqs_queue.ingest.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.ingest.arn
      Condition = { ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.object_created.arn } }
    }]
  })
}

data "aws_iam_policy_document" "assume_lambda" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "worker" {
  name               = "${var.name}-ingest-worker"
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "vpc" {
  role       = aws_iam_role.worker.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy" "worker" {
  role = aws_iam_role.worker.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.landing.arn}/*" },
      { Effect = "Allow", Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"], Resource = aws_sqs_queue.ingest.arn },
      { Effect = "Allow", Action = ["bedrock:InvokeModel"], Resource = "*" },
      { Effect = "Allow", Action = ["aoss:APIAccessAll"], Resource = "*" },
      { Effect = "Allow", Action = ["textract:AnalyzeDocument", "comprehend:DetectPiiEntities"], Resource = "*" },
      { Effect = "Allow", Action = ["kms:Decrypt", "kms:GenerateDataKey"], Resource = var.kms_key_arn }
    ]
  })
}

resource "aws_lambda_function" "worker" {
  function_name = "${var.name}-ingest-worker"
  role          = aws_iam_role.worker.arn
  package_type  = "Image"
  image_uri     = var.image_uri
  timeout       = 900
  memory_size   = 3008
  image_config { command = ["services.ingest_worker.handlers.aws_handler"] }
  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = var.security_group_ids
  }
  environment { variables = var.environment }
  tracing_config { mode = "Active" }
  tags = var.tags
}

resource "aws_lambda_event_source_mapping" "sqs" {
  event_source_arn                   = aws_sqs_queue.ingest.arn
  function_name                      = aws_lambda_function.worker.arn
  batch_size                         = 10
  maximum_batching_window_in_seconds = 30
  function_response_types            = ["ReportBatchItemFailures"]
}

output "landing_bucket" { value = aws_s3_bucket.landing.bucket }
output "worker_role_arn" { value = aws_iam_role.worker.arn }
output "dlq_url" { value = aws_sqs_queue.dlq.id }
