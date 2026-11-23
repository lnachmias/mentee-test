mock_provider "aws" {
  override_during = plan
}

override_resource {
  target          = aws_s3_bucket.state
  override_during = plan
  values = {
    id  = "exam-mentee-test-state-123456789012-eu-west-2"
    arn = "arn:aws:s3:::exam-mentee-test-state-123456789012-eu-west-2"
  }
}

variables {
  aws_account_id = "123456789012"
}

run "secure_workflow_bootstrap" {
  command = plan

  assert {
    condition     = aws_s3_bucket_public_access_block.state.block_public_acls && aws_s3_bucket_public_access_block.state.block_public_policy && aws_s3_bucket_public_access_block.state.ignore_public_acls && aws_s3_bucket_public_access_block.state.restrict_public_buckets
    error_message = "Terraform state must not be public."
  }

  assert {
    condition     = aws_s3_bucket_versioning.state.versioning_configuration[0].status == "Enabled"
    error_message = "State versioning is required for recovery."
  }

  assert {
    condition     = aws_iam_role.terraform.name == "exam-github-terraform" && aws_iam_role.terraform.permissions_boundary == "arn:aws:iam::123456789012:policy/candidate-exam-boundary"
    error_message = "The infrastructure role must obey the exam name and boundary requirements."
  }

  assert {
    condition     = [for statement in jsondecode(aws_iam_role_policy.terraform.policy).Statement : statement.Condition.ArnEquals["iam:PermissionsBoundary"] if statement.Sid == "CreateBoundedReleaseRole"][0] == "arn:aws:iam::123456789012:policy/candidate-exam-boundary"
    error_message = "Terraform must not be able to create an unbounded release role."
  }
}
