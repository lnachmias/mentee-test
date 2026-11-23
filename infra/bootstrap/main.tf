# Run once with the exam operator credentials. The workflow cannot create its
# own initial identity or state backend before it has authenticated.
locals {
  state_bucket = "exam-mentee-test-state-${var.aws_account_id}-eu-west-2"
  boundary     = "arn:aws:iam::${var.aws_account_id}:policy/candidate-exam-boundary"
  release_role = "arn:aws:iam::${var.aws_account_id}:role/exam-github-deploy"
  oidc_arn     = "arn:aws:iam::${var.aws_account_id}:oidc-provider/token.actions.githubusercontent.com"
  repo_parts   = split("/", var.github_repository)
  github_subjects = [
    "repo:${var.github_repository}:ref:refs/heads/main",
    "repo:${local.repo_parts[0]}@*/${local.repo_parts[1]}@*:ref:refs/heads/main",
  ]
}

# Fail during planning if the selected account does not contain the exam boundary.
data "aws_iam_policy" "boundary" {
  arn = local.boundary
}

resource "aws_s3_bucket" "state" {
  bucket        = local.state_bucket
  force_destroy = false
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "RequireTLS", Effect = "Deny", Principal = "*", Action = "s3:*"
      Resource  = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_github_oidc_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

resource "aws_iam_role" "terraform" {
  name                 = "exam-github-terraform"
  permissions_boundary = local.boundary
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Action = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = local.oidc_arn }
      Condition = {
        StringEquals = { "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com" }
        StringLike   = { "token.actions.githubusercontent.com:sub" = local.github_subjects }
      }
    }]
  })
  depends_on = [aws_iam_openid_connect_provider.github, data.aws_iam_policy.boundary]
}

resource "aws_iam_role_policy" "terraform" {
  name = "mentee-infrastructure"
  role = aws_iam_role.terraform.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ListState", Effect = "Allow", Action = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = aws_s3_bucket.state.arn
      },
      {
        Sid = "StateAndLock", Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"]
        Resource = [
          "${aws_s3_bucket.state.arn}/mentee-test/infrastructure.tfstate",
          "${aws_s3_bucket.state.arn}/mentee-test/infrastructure.tfstate.tflock",
        ]
      },
      {
        Sid      = "ReleaseLock", Effect = "Allow", Action = "s3:DeleteObject"
        Resource = "${aws_s3_bucket.state.arn}/mentee-test/infrastructure.tfstate.tflock"
      },
      {
        Sid      = "ManageEcr", Effect = "Allow"
        Resource = "arn:aws:ecr:eu-west-2:${var.aws_account_id}:repository/mentee-test"
        Action = [
          "ecr:CreateRepository", "ecr:DescribeRepositories", "ecr:PutImageTagMutability",
          "ecr:PutImageScanningConfiguration", "ecr:GetRepositoryPolicy", "ecr:SetRepositoryPolicy",
          "ecr:DeleteRepositoryPolicy", "ecr:ListTagsForResource", "ecr:TagResource", "ecr:UntagResource",
        ]
      },
      {
        Sid       = "CreateAndReadLightsail", Effect = "Allow", Resource = "*"
        Action    = ["lightsail:CreateContainerService", "lightsail:GetContainerServices", "lightsail:GetContainerServicePowers"]
        Condition = { StringEquals = { "aws:RequestedRegion" = "eu-west-2" } }
      },
      {
        Sid      = "ConfigureLightsail", Effect = "Allow"
        Resource = "arn:aws:lightsail:eu-west-2:${var.aws_account_id}:ContainerService/*"
        Action   = ["lightsail:UpdateContainerService", "lightsail:TagResource", "lightsail:UntagResource"]
      },
      {
        Sid       = "CreateBoundedReleaseRole", Effect = "Allow", Action = "iam:CreateRole", Resource = local.release_role
        Condition = { ArnEquals = { "iam:PermissionsBoundary" = local.boundary } }
      },
      {
        Sid = "ConfigureReleaseRole", Effect = "Allow", Resource = local.release_role
        Action = [
          "iam:GetRole", "iam:UpdateAssumeRolePolicy", "iam:TagRole", "iam:UntagRole", "iam:ListRoleTags",
          "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies",
        ]
      },
    ]
  })
}
