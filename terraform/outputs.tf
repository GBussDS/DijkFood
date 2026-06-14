# ============================================================
# Outputs — URLs e IDs relevantes pós-deploy
# ============================================================

# ── Rede ─────────────────────────────────────────────────────────────────────

output "vpc_id" {
  description = "ID da VPC principal"
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "IDs das subnets públicas (ALB + NAT)"
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "IDs das subnets privadas (ECS + RDS)"
  value       = aws_subnet.private[*].id
}

output "nat_gateway_public_ip" {
  description = "IP público do NAT Gateway"
  value       = aws_eip.nat.public_ip
}

# ── Security Groups ───────────────────────────────────────────────────────────

output "sg_alb_id" {
  description = "ID do Security Group do ALB"
  value       = aws_security_group.alb.id
}

output "sg_ecs_id" {
  description = "ID do Security Group dos containers ECS"
  value       = aws_security_group.ecs.id
}

output "sg_rds_id" {
  description = "ID do Security Group do RDS"
  value       = aws_security_group.rds.id
}

# ── Ponto de entrada da aplicação ────────────────────────────────────────────

output "alb_dns_name" {
  description = "URL pública do Application Load Balancer (acesse via HTTP)"
  value       = "http://${aws_lb.main.dns_name}"
}

output "frontend_url" {
  description = "URL do frontend estático hospedado no S3"
  value       = "http://${aws_s3_bucket_website_configuration.frontend.website_endpoint}"
}

# ── Banco de Dados ────────────────────────────────────────────────────────────

output "rds_endpoint" {
  description = "Endpoint do RDS PostgreSQL (privado — acessível apenas pelo ECS)"
  value       = aws_db_instance.postgres.address
}

output "rds_port" {
  value = aws_db_instance.postgres.port
}

# ── Streaming ─────────────────────────────────────────────────────────────────

output "kinesis_stream_name" {
  value = aws_kinesis_stream.events.name
}

output "kinesis_stream_arn" {
  value = aws_kinesis_stream.events.arn
}

# ── S3 Buckets ────────────────────────────────────────────────────────────────

output "s3_data_lake_bucket" {
  value = aws_s3_bucket.data_lake.bucket
}

output "s3_models_bucket" {
  value = aws_s3_bucket.models.bucket
}

output "s3_athena_results_bucket" {
  value = aws_s3_bucket.athena_results.bucket
}

output "s3_frontend_bucket" {
  value = aws_s3_bucket.frontend.bucket
}

# ── ECS ──────────────────────────────────────────────────────────────────────

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "ecr_repository_urls" {
  description = "URLs dos repositórios ECR (use para docker push)"
  value       = { for k, v in aws_ecr_repository.services : k => v.repository_url }
}

# ── Analítico ────────────────────────────────────────────────────────────────

output "glue_database_name" {
  value = aws_glue_catalog_database.analytics.name
}

output "athena_workgroup" {
  value = aws_athena_workgroup.main.name
}

# ── Comandos úteis pós-deploy ─────────────────────────────────────────────────

output "next_steps" {
  description = "Próximos passos após o terraform apply"
  value       = <<-EOT
    1. Faça login no ECR e suba as imagens Docker:
       aws ecr get-login-password --region ${var.aws_region} | \
         docker login --username AWS --password-stdin \
         $(terraform output -raw ecr_repository_urls | jq -r '.["order-processor"]' | cut -d/ -f1)

    2. Execute o schema no RDS (via bastion ou ECS exec):
       psql -h ${aws_db_instance.postgres.address} -U ${var.db_username} -d ${var.db_name} -f sql/schema.sql

    3. Acesse a aplicação:
       ALB:      http://${aws_lb.main.dns_name}
       Frontend: http://${aws_s3_bucket_website_configuration.frontend.website_endpoint}

    4. Para treinar modelos ML com dados reais:
       python train_model.py --bucket ${aws_s3_bucket.models.bucket}
  EOT
}
