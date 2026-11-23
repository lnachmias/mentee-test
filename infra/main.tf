locals {
  repository_arn = "arn:aws:ecr:eu-west-2:${var.aws_account_id}:repository/mentee-test"
  oidc_arn       = "arn:aws:iam::${var.aws_account_id}:oidc-provider/token.actions.githubusercontent.com"
  repo_parts     = split("/", var.github_repository)
  # Support both legacy and newer GitHub subjects that include immutable IDs.
  github_subjects = [
    "repo:${var.github_repository}:ref:refs/heads/main",
    "repo:${local.repo_parts[0]}@*/${local.repo_parts[1]}@*:ref:refs/heads/main",
  ]
}

resource "aws_ecr_repository" "app" {
  name                 = "mentee-test"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  lifecycle {
    prevent_destroy = true
  }
}

# Terraform owns capacity and private-registry access. CI owns image deployments.
resource "aws_lightsail_container_service" "app" {
  name        = "mentee-test"
  power       = var.lightsail_power
  scale       = var.lightsail_scale
  is_disabled = false

  private_registry_access {
    ecr_image_puller_role {
      is_active = true
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_ecr_repository_policy" "lightsail_pull" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "LightsailPull"
      Effect    = "Allow"
      Principal = { AWS = aws_lightsail_container_service.app.private_registry_access[0].ecr_image_puller_role[0].principal_arn }
      Action    = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
    }]
  })
}

resource "aws_iam_role" "release" {
  name                 = "exam-github-deploy"
  permissions_boundary = "arn:aws:iam::${var.aws_account_id}:policy/candidate-exam-boundary"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = local.oidc_arn }
      Condition = {
        StringEquals = { "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com" }
        StringLike   = { "token.actions.githubusercontent.com:sub" = local.github_subjects }
      }
    }]
  })
}

resource "aws_iam_role_policy" "release" {
  name = "mentee-release"
  role = aws_iam_role.release.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EcrLogin", Effect = "Allow", Action = "ecr:GetAuthorizationToken", Resource = "*"
        Condition = { StringEquals = { "aws:RequestedRegion" = "eu-west-2" } }
      },
      {
        Sid = "PublishImages", Effect = "Allow", Resource = local.repository_arn
        Action = [
          "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer", "ecr:DescribeImages", "ecr:ListImages", "ecr:DescribeRepositories",
        ]
      },
      {
        Sid      = "Deploy", Effect = "Allow", Action = "lightsail:CreateContainerServiceDeployment"
        Resource = aws_lightsail_container_service.app.arn
      },
      {
        Sid       = "ReadDeployments", Effect = "Allow", Resource = "*"
        Action    = ["lightsail:GetContainerServices", "lightsail:GetContainerServiceDeployments", "lightsail:GetContainerLog"]
        Condition = { StringEquals = { "aws:RequestedRegion" = "eu-west-2" } }
      },
    ]
  })
}
