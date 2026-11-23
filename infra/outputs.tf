output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "lightsail_service_name" {
  value = aws_lightsail_container_service.app.name
}

output "service_url" {
  value = aws_lightsail_container_service.app.url
}

output "release_role_arn" {
  value = aws_iam_role.release.arn
}
