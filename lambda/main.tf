# TODO: disable retry when applicable.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 4"
    }
  }
}

locals {
  empty_source      = "${path.module}/empty.zip"
  empty_source_hash = replace(replace(replace(filebase64sha256(local.empty_source), "=", ""), "/", "_"), "+", "-")

  source_code_digest = aws_s3_object.function_code.metadata.digest
  padding_length     = floor((length(local.source_code_digest) + 3) / 4) * 4 - length(local.source_code_digest)
  source_code_hash   = "${replace(replace(aws_s3_object.function_code.metadata.digest, "-", "+"), "_", "/")}${substr("===", 0, local.padding_length)}"
}

resource "aws_cloudwatch_log_group" "logs" {
  name              = "/aws/lambda/${var.name}"
  retention_in_days = var.logs_retention_in_days
}

resource "aws_s3_object" "function_code" {
  bucket = var.deploy_bucket
  key    = "${var.name}.zip"
  source = "${path.module}/empty.zip"

  metadata = {
    digest   = local.empty_source_hash
    revision = "initial"
  }

  lifecycle {
    ignore_changes = [
      source,
      metadata,
    ]
  }
}

resource "aws_lambda_function" "function" {
  function_name = var.name
  description   = aws_s3_object.function_code.metadata.revision
  role          = var.role_arn

  memory_size = var.memory_size
  runtime     = var.runtime
  handler     = var.handler
  timeout     = var.timeout

  s3_bucket         = aws_s3_object.function_code.bucket
  s3_key            = aws_s3_object.function_code.key
  s3_object_version = aws_s3_object.function_code.version_id
  source_code_hash  = local.source_code_hash
  publish           = true

  dynamic "environment" {
    for_each = range(signum(length(var.env_vars)))

    content {
      variables = var.env_vars
    }
  }
}

resource "aws_lambda_alias" "alias" {
  name             = "current"
  description      = "Current invocation alias for ${var.name}"
  function_name    = aws_lambda_function.function.function_name
  function_version = aws_lambda_function.function.version

  lifecycle {
    ignore_changes = [
      function_version,
      routing_config,
    ]
  }
}

output "logs_arn" {
  value = aws_cloudwatch_log_group.logs.arn
}

output "function_arn" {
  value = aws_lambda_function.function.arn
}

output "function_qualifier" {
  value = aws_lambda_alias.alias.name
}

output "invoke_arn" {
  value = aws_lambda_alias.alias.arn
}
