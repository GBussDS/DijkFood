# ============================================================
# Camada de Computação: ECR + ECS Fargate + ALB + Auto Scaling
# ============================================================

# ── ECR: um repositório por microsserviço ─────────────────────────────────────

resource "aws_ecr_repository" "services" {
  for_each = { for svc in var.services : svc.name => svc }

  name                 = "${var.project}/${each.key}"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Name = "${var.project}-${each.key}" }
}

# ── ECS Cluster com Container Insights habilitado ─────────────────────────────

resource "aws_ecs_cluster" "main" {
  name = "${var.project}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = { Name = "${var.project}-cluster" }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = 1
  }
}

# ── CloudWatch Log Groups (um por serviço) ────────────────────────────────────

resource "aws_cloudwatch_log_group" "ecs" {
  for_each = { for svc in var.services : svc.name => svc }

  name              = "/ecs/${var.project}/${each.key}"
  retention_in_days = 7

  tags = { Service = each.key }
}

# ── Task Definitions ──────────────────────────────────────────────────────────
# As variáveis de ambiente espelham exatamente o que infra/compute.py injeta.

locals {
  db_services        = ["order-processor", "order-management", "conversational", "dashboard-analytics"]
  ml_caller_services = ["order-processor", "conversational"]
  bedrock_service    = "conversational"
  dashboard_service  = "dashboard-analytics"
}

resource "aws_ecs_task_definition" "services" {
  for_each = { for svc in var.services : svc.name => svc }

  family                   = "${var.project}-${each.key}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = data.aws_iam_role.lab_role.arn
  task_role_arn            = data.aws_iam_role.lab_role.arn

  container_definitions = jsonencode([{
    name      = each.key
    image     = "${aws_ecr_repository.services[each.key].repository_url}:latest"
    essential = true

    portMappings = [{
      containerPort = each.value.port
      hostPort      = each.value.port
      protocol      = "tcp"
    }]

    environment = concat(
      # Variáveis comuns a todos os serviços
      [
        { name = "AWS_REGION", value = var.aws_region },
        { name = "KINESIS_STREAM", value = aws_kinesis_stream.events.name },
        { name = "DYNAMODB_TABLE", value = aws_dynamodb_table.courier_positions.name },
      ],
      # Variáveis de banco — apenas serviços que conectam ao RDS
      contains(local.db_services, each.key) ? [
        { name = "DB_HOST", value = aws_db_instance.postgres.address },
        { name = "DB_PORT", value = tostring(aws_db_instance.postgres.port) },
        { name = "DB_NAME", value = var.db_name },
        { name = "DB_USER", value = var.db_username },
        { name = "DB_PASS", value = var.db_password },
      ] : [],
      # URL do ML Inference — order-processor e conversational consultam
      contains(local.ml_caller_services, each.key) ? [
        { name = "ML_INFERENCE_URL", value = "http://localhost:8004" },
      ] : [],
      # Credenciais Bedrock + Athena — somente o serviço conversational
      each.key == local.bedrock_service ? [
        { name = "BEDROCK_MODEL_ID", value = var.bedrock_model_id },
        { name = "BEDROCK_AWS_ACCESS_KEY_ID", value = var.bedrock_access_key_id },
        { name = "BEDROCK_AWS_SECRET_ACCESS_KEY", value = var.bedrock_secret_access_key },
        { name = "ATHENA_DATABASE", value = aws_glue_catalog_database.analytics.name },
        { name = "ATHENA_OUTPUT", value = "s3://${aws_s3_bucket.athena_results.bucket}/" },
      ] : [],
      # Athena + tabela de anomalias — somente o dashboard-analytics
      each.key == local.dashboard_service ? [
        { name = "ATHENA_DATABASE", value = aws_glue_catalog_database.analytics.name },
        { name = "ATHENA_OUTPUT", value = "s3://${aws_s3_bucket.athena_results.bucket}/" },
        { name = "DYNAMODB_TABLE", value = aws_dynamodb_table.anomalies.name },
      ] : [],
      # Bucket de modelos — somente o ml-inference
      each.key == "ml-inference" ? [
        { name = "MODELS_BUCKET", value = aws_s3_bucket.models.bucket },
      ] : [],
    )

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.ecs[each.key].name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "ecs"
      }
    }
  }])

  tags = { Service = each.key }
}

# ── Application Load Balancer ─────────────────────────────────────────────────

resource "aws_lb" "main" {
  name               = "${var.project}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  tags = { Name = "${var.project}-alb" }
}

# Target Group individual para cada serviço (tipo IP — obrigatório no Fargate awsvpc)
resource "aws_lb_target_group" "services" {
  for_each = { for svc in var.services : svc.name => svc }

  name        = "tg-${substr(each.key, 0, 28)}"
  port        = each.value.port
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path                = "/health"
    protocol            = "HTTP"
    interval            = 30
    timeout             = 10
    healthy_threshold   = 2
    unhealthy_threshold = 5
    matcher             = "200"
  }

  tags = { Name = "${var.project}-tg-${each.key}" }
}

# Listener HTTP:80 — default retorna 404
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "application/json"
      message_body = jsonencode({ error = "Route not found" })
      status_code  = "404"
    }
  }
}

# Listener Rules de roteamento por path (uma regra por serviço)
resource "aws_lb_listener_rule" "services" {
  for_each = { for svc in var.services : svc.name => svc }

  listener_arn = aws_lb_listener.http.arn
  priority     = each.value.priority

  condition {
    path_pattern {
      values = each.value.path_patterns
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.services[each.key].arn
  }
}

# ── ECS Services ──────────────────────────────────────────────────────────────

resource "aws_ecs_service" "services" {
  for_each = { for svc in var.services : svc.name => svc }

  name                              = "${var.project}-${each.key}"
  cluster                           = aws_ecs_cluster.main.id
  task_definition                   = aws_ecs_task_definition.services[each.key].arn
  desired_count                     = each.value.min_tasks
  launch_type                       = "FARGATE"
  health_check_grace_period_seconds = 60

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.services[each.key].arn
    container_name   = each.key
    container_port   = each.value.port
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  deployment_controller {
    type = "ECS"
  }

  # Ignora mudanças de desired_count — o Auto Scaling controla isso em runtime
  lifecycle {
    ignore_changes = [desired_count]
  }

  depends_on = [aws_lb_listener.http]
}

# ── Application Auto Scaling por CPU ─────────────────────────────────────────

resource "aws_appautoscaling_target" "ecs" {
  for_each = { for svc in var.services : svc.name => svc }

  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.services[each.key].name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = each.value.min_tasks
  max_capacity       = each.value.max_tasks

  depends_on = [aws_ecs_service.services]
}

resource "aws_appautoscaling_policy" "cpu" {
  for_each = { for svc in var.services : svc.name => svc }

  name               = "${var.project}-${each.key}-cpu-scaling"
  service_namespace  = "ecs"
  resource_id        = aws_appautoscaling_target.ecs[each.key].resource_id
  scalable_dimension = aws_appautoscaling_target.ecs[each.key].scalable_dimension
  policy_type        = "TargetTrackingScaling"

  target_tracking_scaling_policy_configuration {
    target_value = each.value.scaling_target

    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }

    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}
