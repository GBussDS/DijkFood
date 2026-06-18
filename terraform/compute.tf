# ============================================================
# Camada de Computação: ECR + ECS Fargate + ALB + Auto Scaling
# ============================================================

locals {
  # Apenas os serviços listados em var.enabled_services são provisionados.
  # Remova um nome da lista (ou via -var) para destruir somente aquele serviço.
  active_services = {
    for svc in var.services : svc.name => svc
    if contains(var.enabled_services, svc.name)
  }

  db_services        = ["order-processor", "order-management", "conversational", "dashboard-analytics"]
  ml_caller_services = ["order-processor", "conversational"]
  bedrock_service    = "conversational"
  dashboard_service  = "dashboard-analytics"
}

# ── ECR: um repositório por microsserviço (sempre criado, independente de enabled) ──

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

# ── ECS Cluster ───────────────────────────────────────────────────────────────

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

# ── CloudWatch Log Groups — somente para serviços ativos ──────────────────────

resource "aws_cloudwatch_log_group" "ecs" {
  for_each = local.active_services

  name              = "/ecs/${var.project}/${each.key}"
  retention_in_days = 7

  tags = { Service = each.key }
}

# ── Task Definitions — somente para serviços ativos ───────────────────────────

resource "aws_ecs_task_definition" "services" {
  for_each = local.active_services

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
      [
        { name = "AWS_REGION", value = var.aws_region },
        { name = "KINESIS_STREAM", value = aws_kinesis_stream.events.name },
        { name = "DYNAMODB_TABLE", value = aws_dynamodb_table.courier_positions.name },
      ],
      contains(local.db_services, each.key) ? [
        { name = "DB_HOST", value = aws_db_instance.postgres.address },
        { name = "DB_PORT", value = tostring(aws_db_instance.postgres.port) },
        { name = "DB_NAME", value = var.db_name },
        { name = "DB_USER", value = var.db_username },
        { name = "DB_PASS", value = var.db_password },
      ] : [],
      contains(local.ml_caller_services, each.key) ? [
        { name = "ML_INFERENCE_URL", value = "http://${aws_lb.main.dns_name}" },
      ] : [],
      each.key == local.bedrock_service ? [
        { name = "BEDROCK_MODEL_ID", value = var.bedrock_model_id },
        { name = "BEDROCK_AWS_ACCESS_KEY_ID", value = var.bedrock_access_key_id },
        { name = "BEDROCK_AWS_SECRET_ACCESS_KEY", value = var.bedrock_secret_access_key },
        { name = "BEDROCK_AWS_SESSION_TOKEN", value = var.bedrock_session_token },
        { name = "ATHENA_DATABASE", value = aws_glue_catalog_database.analytics.name },
        { name = "ATHENA_OUTPUT", value = "s3://${aws_s3_bucket.athena_results.bucket}/" },
      ] : [],
      each.key == local.dashboard_service ? [
        { name = "ATHENA_DATABASE", value = aws_glue_catalog_database.analytics.name },
        { name = "ATHENA_OUTPUT", value = "s3://${aws_s3_bucket.athena_results.bucket}/" },
        { name = "DYNAMODB_TABLE", value = aws_dynamodb_table.anomalies.name },
      ] : [],
      each.key == "ml-inference" ? [
        { name = "MODELS_BUCKET", value = aws_s3_bucket.models.bucket },
      ] : [],
    )

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = "/ecs/${var.project}/${each.key}"
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "ecs"
      }
    }
  }])

  tags = { Service = each.key }
}

# ── Application Load Balancer (sempre criado) ──────────────────────────────────

resource "aws_lb" "main" {
  name               = "${var.project}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  tags = { Name = "${var.project}-alb" }
}

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

# ── Target Groups — somente para serviços ativos ─────────────────────────────

resource "aws_lb_target_group" "services" {
  for_each = local.active_services

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

# ── Listener Rules — somente para serviços ativos ────────────────────────────

resource "aws_lb_listener_rule" "services" {
  for_each = local.active_services

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

# ── ECS Services — somente para serviços ativos ───────────────────────────────

resource "aws_ecs_service" "services" {
  for_each = local.active_services

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

  # Auto Scaling controla desired_count em runtime
  lifecycle {
    ignore_changes = [desired_count]
  }

  depends_on = [aws_lb_listener.http]
}

# ── Auto Scaling — somente para serviços ativos ───────────────────────────────

resource "aws_appautoscaling_target" "ecs" {
  for_each = local.active_services

  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.services[each.key].name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = each.value.min_tasks
  max_capacity       = each.value.max_tasks

  depends_on = [aws_ecs_service.services]
}

resource "aws_appautoscaling_policy" "cpu" {
  for_each = local.active_services

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
