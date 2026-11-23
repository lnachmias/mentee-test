variable "aws_account_id" {
  description = "Expected AWS exam account; prevents applying to another account."
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "Provide the 12-digit AWS exam account ID."
  }
}

variable "github_repository" {
  type        = string
  description = "GitHub owner/repository allowed to release from main."
  default     = "lnachmias/mentee-test"
  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "Use owner/repository, without wildcards."
  }
}

variable "lightsail_power" {
  type        = string
  description = "CPU/memory tier. Nano is sufficient for this demonstration."
  default     = "nano"
  validation {
    condition     = contains(["nano", "micro", "small", "medium", "large", "xlarge"], var.lightsail_power)
    error_message = "Choose a supported Lightsail container power."
  }
}

variable "lightsail_scale" {
  type        = number
  description = "Number of container nodes, from 1 to 20."
  default     = 1
  validation {
    condition     = var.lightsail_scale >= 1 && var.lightsail_scale <= 20 && floor(var.lightsail_scale) == var.lightsail_scale
    error_message = "Scale must be an integer from 1 to 20."
  }
}
