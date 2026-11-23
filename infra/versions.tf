terraform {
  required_version = ">= 1.10, < 2.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  backend "s3" {
    key          = "mentee-test/infrastructure.tfstate"
    region       = "eu-west-2"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region              = "eu-west-2"
  allowed_account_ids = [var.aws_account_id]
  default_tags {
    tags = { Project = "mentee-test", ManagedBy = "Terraform" }
  }
}
