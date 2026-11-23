# Mocked plans only: these tests never create AWS resources.
mock_provider "aws" {
  override_during = plan
}

variables {
  aws_account_id = "123456789012"
}

run "secure_container_infrastructure" {
  command = plan

  assert {
    condition     = aws_ecr_repository.app.image_tag_mutability == "IMMUTABLE" && aws_ecr_repository.app.force_delete == false
    error_message = "Published release tags must not be overwritten or force-deleted."
  }

  assert {
    condition     = aws_lightsail_container_service.app.private_registry_access[0].ecr_image_puller_role[0].is_active
    error_message = "Lightsail must have private ECR pull access enabled."
  }

  assert {
    condition     = aws_iam_role.release.permissions_boundary == "arn:aws:iam::123456789012:policy/candidate-exam-boundary"
    error_message = "The release role must use the exam boundary."
  }

  assert {
    condition     = jsondecode(aws_iam_role.release.assume_role_policy).Statement[0].Condition.StringLike["token.actions.githubusercontent.com:sub"][0] == "repo:lnachmias/mentee-test:ref:refs/heads/main"
    error_message = "GitHub OIDC must be restricted to this repository's main branch."
  }

}
