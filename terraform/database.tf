# ============================================================
# Bancos de Dados Transacionais: RDS PostgreSQL + DynamoDB
# ============================================================

# ── Subnet Group para o RDS ficar nas subnets privadas ───────────────────────

resource "aws_db_subnet_group" "main" {
  name        = "${var.project}-db-subnet-group"
  description = "Subnets privadas para o RDS PostgreSQL"
  subnet_ids  = aws_subnet.private[*].id

  tags = { Name = "${var.project}-db-subnet-group" }
}

# ── RDS PostgreSQL 16 ─────────────────────────────────────────────────────────

resource "aws_db_instance" "postgres" {
  identifier = "${var.project}-postgres"

  engine         = "postgres"
  engine_version = "16.3"
  instance_class = var.db_instance_class

  db_name  = var.db_name
  username = var.db_username
  password = var.db_password

  allocated_storage = 20
  storage_type      = "gp2"
  storage_encrypted = true

  # Sem Multi-AZ — reduz custo no laboratório
  multi_az            = false
  publicly_accessible = false
  deletion_protection = false
  skip_final_snapshot = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  # PostGIS é habilitado via parâmetro de cluster, não via engine version;
  # as extensões são criadas no schema.sql após o provisionamento.
  parameter_group_name = aws_db_parameter_group.postgres.name

  tags = { Name = "${var.project}-postgres" }
}

resource "aws_db_parameter_group" "postgres" {
  name        = "${var.project}-pg16"
  family      = "postgres16"
  description = "Parametros DijkFood para PostgreSQL 16"

  tags = { Name = "${var.project}-pg16" }
}

# ── DynamoDB: posições GPS dos entregadores ───────────────────────────────────

resource "aws_dynamodb_table" "courier_positions" {
  name         = "courier_positions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "courier_id"

  attribute {
    name = "courier_id"
    type = "S"
  }

  # TTL automático: itens expiram em 24h sem custo adicional
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = { Name = "${var.project}-courier-positions" }
}

# ── DynamoDB: anomalias operacionais detectadas pela Lambda ───────────────────

resource "aws_dynamodb_table" "anomalies" {
  name         = "anomalies"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = { Name = "${var.project}-anomalies" }
}

# ── DynamoDB: médias históricas usadas pelo Anomaly Detector ─────────────────

resource "aws_dynamodb_table" "historical_averages" {
  name         = "historical_averages"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "metric"
  range_key    = "dimension"

  attribute {
    name = "metric"
    type = "S"
  }

  attribute {
    name = "dimension"
    type = "S"
  }

  tags = { Name = "${var.project}-historical-averages" }
}
