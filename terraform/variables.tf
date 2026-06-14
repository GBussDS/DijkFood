variable "aws_region" {
  description = "Região AWS principal"
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "Perfil AWS principal (AWS Academy / LabRole)"
  type        = string
  default     = "default"
}

variable "bedrock_profile" {
  description = "Perfil AWS secundário para o Amazon Bedrock (conta pessoal)"
  type        = string
  default     = "bedrock"
}

variable "project" {
  description = "Prefixo de todos os recursos"
  type        = string
  default     = "dijkfood"
}

variable "iam_role_name" {
  description = "Nome da IAM Role que os containers ECS e Lambdas usam"
  type        = string
  default     = "LabRole"
}

# ── Rede ─────────────────────────────────────────────────────────────────────

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "public_subnet_cidrs" {
  type    = list(string)
  default = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "private_subnet_cidrs" {
  type    = list(string)
  default = ["10.0.3.0/24", "10.0.4.0/24"]
}

# ── Banco de Dados ────────────────────────────────────────────────────────────

variable "db_instance_class" {
  type    = string
  default = "db.t3.micro"
}

variable "db_name" {
  type    = string
  default = "dijkfood"
}

variable "db_username" {
  type    = string
  default = "dijkfood"
}

variable "db_password" {
  description = "Senha do banco RDS (não use o default em produção)"
  type        = string
  default     = "dijkfood2024"
  sensitive   = true
}

# ── Kinesis ──────────────────────────────────────────────────────────────────

variable "kinesis_shard_count" {
  type    = number
  default = 4
}

# ── ECS ──────────────────────────────────────────────────────────────────────

variable "services" {
  description = "Definições de cada microsserviço ECS"
  type = list(object({
    name           = string
    port           = number
    cpu            = string
    memory         = string
    path_patterns  = list(string)
    priority       = number
    min_tasks      = number
    max_tasks      = number
    scaling_target = number
  }))
  default = [
    {
      name           = "order-processor"
      port           = 8000
      cpu            = "1024"
      memory         = "4096"
      path_patterns  = ["/api/orders*"]
      priority       = 1
      min_tasks      = 2
      max_tasks      = 10
      scaling_target = 70
    },
    {
      name           = "order-management"
      port           = 8001
      cpu            = "512"
      memory         = "1024"
      path_patterns  = ["/api/customers*", "/api/restaurants*", "/api/couriers*"]
      priority       = 2
      min_tasks      = 2
      max_tasks      = 8
      scaling_target = 70
    },
    {
      name           = "position-tracker"
      port           = 8002
      cpu            = "512"
      memory         = "1024"
      path_patterns  = ["/api/positions*"]
      priority       = 3
      min_tasks      = 2
      max_tasks      = 15
      scaling_target = 60
    },
    {
      name           = "conversational"
      port           = 8003
      cpu            = "1024"
      memory         = "2048"
      path_patterns  = ["/api/chat*"]
      priority       = 4
      min_tasks      = 1
      max_tasks      = 4
      scaling_target = 70
    },
    {
      name           = "ml-inference"
      port           = 8004
      cpu            = "512"
      memory         = "1024"
      path_patterns  = ["/api/predictions*"]
      priority       = 5
      min_tasks      = 1
      max_tasks      = 4
      scaling_target = 70
    },
    {
      name           = "dashboard-analytics"
      port           = 8005
      cpu            = "512"
      memory         = "1024"
      path_patterns  = ["/api/dashboard*"]
      priority       = 6
      min_tasks      = 1
      max_tasks      = 4
      scaling_target = 70
    },
  ]
}

# ── Bedrock ──────────────────────────────────────────────────────────────────

variable "bedrock_access_key_id" {
  description = "Access Key ID da conta pessoal para o Bedrock (injetado no ECS)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "bedrock_secret_access_key" {
  description = "Secret Access Key da conta pessoal para o Bedrock"
  type        = string
  default     = ""
  sensitive   = true
}

variable "bedrock_model_id" {
  type    = string
  default = "amazon.nova-micro-v1:0"
}
