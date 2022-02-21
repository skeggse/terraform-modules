terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 3"
    }
  }

  experiments = [module_variable_optional_attrs]
}

data "aws_iam_policy_document" "assume_policy" {
  dynamic "statement" {
    for_each = var.assume_role_principals

    content {
      effect = "Allow"
      principals {
        type        = statement.value.type
        identifiers = statement.value.identifiers
      }
      actions = [
        can(regex("^arn:aws:iam::[^:]*:oidc-provider/", tolist(statement.value.identifiers)[0]))
        ? "sts:AssumeRoleWithWebIdentity"
        : "sts:AssumeRole"
      ]

      dynamic "condition" {
        for_each = coalesce(statement.value.conditions, [])

        content {
          test     = condition.value.test
          variable = condition.value.variable
          values   = condition.value.values
        }
      }
    }
  }
}

resource "aws_iam_policy" "policy" {
  name        = var.name
  description = "Policy for ${var.description}"
  policy      = var.policy
}

resource "aws_iam_role" "role" {
  name               = var.name
  description        = "Role for ${var.description}"
  assume_role_policy = data.aws_iam_policy_document.assume_policy.json
}

resource "aws_iam_role_policy_attachment" "attach" {
  role       = aws_iam_role.role.name
  policy_arn = aws_iam_policy.policy.arn
}

output "arn" {
  value = aws_iam_role.role.arn
}
