variable "name" {
  description = "The name of the AWS IAM Role"
}

variable "description" {
  description = "The role's description"
  default     = "an unspecified system"
}

variable "policy" {
  description = "The policy document for the role"
}

variable "assume_role_principals" {
  description = "The principals that may assume the role"
  type = list(
    object({
      type        = string
      identifiers = set(string)
      conditions = optional(
        list(
          object({
            test     = string
            variable = string
            values   = set(string)
          })
        )
      )
    })
  )
}
