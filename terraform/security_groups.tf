# ============================================================
# Security Groups
# Fluxo de tráfego: Internet → ALB → ECS → RDS
# ============================================================

# ── SG do ALB — aceita HTTP/HTTPS da internet ─────────────────────────────────

resource "aws_security_group" "alb" {
  name        = "${var.project}-sg-alb"
  description = "ALB: aceita HTTP e HTTPS da internet"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project}-sg-alb" }
}

# ── SG do ECS — aceita tráfego somente do ALB, nas portas dos microsserviços ──

resource "aws_security_group" "ecs" {
  name        = "${var.project}-sg-ecs"
  description = "ECS Fargate: aceita somente do ALB nas portas 8000-8005"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Microsservicos via ALB"
    from_port       = 8000
    to_port         = 8005
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  # Comunicação interna entre containers (service-to-service: order-processor → ml-inference)
  ingress {
    description = "Comunicacao interna ECS"
    from_port   = 8000
    to_port     = 8005
    protocol    = "tcp"
    self        = true
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project}-sg-ecs" }
}

# ── SG do RDS — aceita PostgreSQL somente do ECS ─────────────────────────────

resource "aws_security_group" "rds" {
  name        = "${var.project}-sg-rds"
  description = "RDS PostgreSQL: aceita somente do ECS na porta 5432"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "PostgreSQL via ECS"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project}-sg-rds" }
}
