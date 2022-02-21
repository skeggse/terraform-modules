variable "name" {
  description = "The function's name"
}

variable "role_arn" {
  description = "The function's IAM role ARN"
}

variable "memory_size" {
  description = "The amount of memory to provide to the function"
  type        = number
  default     = 256
}

# TODO: pull handler, runtime from object metadata
variable "handler" {
  description = "The handler path for the Lambda runtime to invoke"
}

variable "runtime" {
  description = "The Lambda runtime to use for the function"
}

variable "env_vars" {
  description = "Environment variables to provide to the function"
  type        = map(string)
  default     = {}
}

variable "deploy_bucket" {
  description = "The bucket that hosts the function's code"
}

variable "timeout" {
  description = "The function timeout, in seconds"
  default     = 10
  type        = number
}
