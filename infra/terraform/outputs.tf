output "alb_dns_name" {
  value = aws_lb.web.dns_name
}

output "artifacts_bucket" {
  value = aws_s3_bucket.artifacts.bucket
}

output "web_repository_url" {
  value = aws_ecr_repository.web.repository_url
}

output "sim_repository_url" {
  value = aws_ecr_repository.sim.repository_url
}

output "db_endpoint" {
  value = aws_db_instance.main.address
}

output "redis_endpoint" {
  value = aws_elasticache_replication_group.main.primary_endpoint_address
}

output "db_secret_arn" {
  value = aws_secretsmanager_secret.db.arn
}
