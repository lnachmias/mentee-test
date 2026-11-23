output "github_repository_variables" {
  description = "Add these under GitHub Settings > Secrets and variables > Actions > Variables."
  value = {
    AWS_ACCOUNT_ID  = var.aws_account_id
    TF_STATE_BUCKET = aws_s3_bucket.state.id
  }
}

output "terraform_role_arn" {
  value = aws_iam_role.terraform.arn
}
