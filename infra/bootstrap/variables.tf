variable "aws_account_id" {
  type        = string
  description = "Expected 12-digit AWS exam account ID."
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "Provide the 12-digit AWS exam account ID."
  }
}

variable "github_repository" {
  type    = string
  default = "lnachmias/mentee-test"
  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "Use owner/repository, without wildcards."
  }
}

variable "create_github_oidc_provider" {
  type        = bool
  description = "Set false if this account already has GitHub's OIDC provider."
  default     = true
}
