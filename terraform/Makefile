## DijkFood — Makefile
## Uso: make <target> [SERVICE=nome-do-servico] [COUNT=n]
##
## Serviços disponíveis:
##   order-processor  order-management  position-tracker
##   conversational   ml-inference      dashboard-analytics

REGION  ?= us-east-1
PROJECT ?= dijkfood
CLUSTER  = $(PROJECT)-cluster

# Cores ANSI
GREEN  = \033[0;32m
YELLOW = \033[0;33m
RED    = \033[0;31m
NC     = \033[0m

.DEFAULT_GOAL := help

# ============================================================
# AJUDA
# ============================================================

.PHONY: help
help:
	@printf "\n$(GREEN)DijkFood — comandos disponíveis$(NC)\n\n"
	@printf "$(YELLOW)Infraestrutura completa$(NC)\n"
	@printf "  make init          Inicializa providers do Terraform\n"
	@printf "  make plan          Mostra o que será criado/destruído\n"
	@printf "  make up            Cria toda a infraestrutura + containers\n"
	@printf "  make down          Destrói tudo (evita cobranças)\n\n"
	@printf "$(YELLOW)Imagens Docker$(NC)\n"
	@printf "  make push              Build e push de todos os serviços para o ECR\n"
	@printf "  make push SERVICE=x    Build e push de um serviço específico\n\n"
	@printf "$(YELLOW)Controle individual de serviços$(NC)\n"
	@printf "  make service-stop    SERVICE=x   Para um container (desired=0, rápido)\n"
	@printf "  make service-start   SERVICE=x   Retoma um container (desired=min_tasks)\n"
	@printf "  make service-restart SERVICE=x   Para e reinicia (força novo deploy)\n"
	@printf "  make service-create  SERVICE=x   Provisiona um serviço via Terraform\n"
	@printf "  make service-destroy SERVICE=x   Remove um serviço via Terraform\n\n"
	@printf "$(YELLOW)Utilitários$(NC)\n"
	@printf "  make status            Status de todos os serviços ECS\n"
	@printf "  make logs SERVICE=x    Tail dos logs CloudWatch de um serviço\n"
	@printf "  make schema            Executa sql/schema.sql no RDS via Fargate\n"
	@printf "  make frontend          Faz upload do frontend/  para o bucket S3\n"
	@printf "  make outputs           Mostra todos os outputs do Terraform\n\n"

# ============================================================
# INFRAESTRUTURA COMPLETA
# ============================================================

.PHONY: init
init:
	@printf "$(GREEN)▶ terraform init$(NC)\n"
	terraform init

.PHONY: plan
plan:
	@printf "$(GREEN)▶ terraform plan$(NC)\n"
	terraform plan

.PHONY: up
up: init
	@printf "$(GREEN)▶ Criando infraestrutura completa...$(NC)\n"
	terraform apply -auto-approve
	@$(MAKE) frontend
	@printf "\n$(GREEN)✓ Infraestrutura no ar. Próximo passo: make push$(NC)\n"

.PHONY: down
down:
	@printf "$(RED)▶ Destruindo toda a infraestrutura...$(NC)\n"
	terraform destroy -auto-approve
	@printf "$(GREEN)✓ Infraestrutura destruída.$(NC)\n"

# ============================================================
# IMAGENS DOCKER
# ============================================================

.PHONY: push
push:
	@if [ -z "$(SERVICE)" ]; then \
		printf "$(GREEN)▶ Build e push de todos os serviços...$(NC)\n"; \
		bash ../scripts/push_images.sh; \
	else \
		printf "$(GREEN)▶ Build e push: $(SERVICE)$(NC)\n"; \
		bash ../scripts/push_images.sh $(SERVICE); \
	fi

# ============================================================
# CONTROLE INDIVIDUAL — via AWS CLI (sem tocar no Terraform)
# Esses comandos são instantâneos: alteram apenas o desired_count do ECS.
# ============================================================

.PHONY: service-stop
service-stop:
	@[ -n "$(SERVICE)" ] || (printf "$(RED)Erro: passe SERVICE=nome-do-servico$(NC)\n" && exit 1)
	@printf "$(YELLOW)▶ Parando $(SERVICE) (desired=0)...$(NC)\n"
	@aws ecs update-service \
		--cluster $(CLUSTER) \
		--service $(PROJECT)-$(SERVICE) \
		--desired-count 0 \
		--region $(REGION) \
		--output json \
	| jq -r '"  " + .service.serviceName + " → desejado: " + (.service.desiredCount | tostring) + " tarefa(s)"'
	@printf "$(GREEN)✓ $(SERVICE) parado.$(NC)\n"

.PHONY: service-start
service-start:
	@[ -n "$(SERVICE)" ] || (printf "$(RED)Erro: passe SERVICE=nome-do-servico$(NC)\n" && exit 1)
	$(eval COUNT := $(shell terraform output -json services_config 2>/dev/null | jq -r --arg s "$(SERVICE)" '.[$s].min_tasks // 1' 2>/dev/null | head -1 | tr -d '[:space:]' || echo 1))
	@printf "$(GREEN)▶ Iniciando $(SERVICE) (desired=$(COUNT))...$(NC)\n"
	@aws ecs update-service \
		--cluster $(CLUSTER) \
		--service $(PROJECT)-$(SERVICE) \
		--desired-count $(COUNT) \
		--force-new-deployment \
		--region $(REGION) \
		--output json \
	| jq -r '"  " + .service.serviceName + " → desejado: " + (.service.desiredCount | tostring) + " tarefa(s)"'
	@printf "$(GREEN)✓ $(SERVICE) iniciado.$(NC)\n"

.PHONY: service-restart
service-restart:
	@[ -n "$(SERVICE)" ] || (printf "$(RED)Erro: passe SERVICE=nome-do-servico$(NC)\n" && exit 1)
	@printf "$(YELLOW)▶ Reiniciando $(SERVICE)...$(NC)\n"
	@$(MAKE) service-stop SERVICE=$(SERVICE)
	@sleep 3
	@$(MAKE) service-start SERVICE=$(SERVICE)

# ============================================================
# CONTROLE INDIVIDUAL — via Terraform (cria/destrói recursos)
# Use quando quiser alterar configuração (CPU, memória, env vars).
# ============================================================

.PHONY: service-create
service-create:
	@[ -n "$(SERVICE)" ] || (printf "$(RED)Erro: passe SERVICE=nome-do-servico$(NC)\n" && exit 1)
	@printf "$(GREEN)▶ Provisionando serviço: $(SERVICE)$(NC)\n"
	terraform apply -auto-approve \
		-target='aws_cloudwatch_log_group.ecs["$(SERVICE)"]' \
		-target='aws_ecs_task_definition.services["$(SERVICE)"]' \
		-target='aws_lb_target_group.services["$(SERVICE)"]' \
		-target='aws_lb_listener_rule.services["$(SERVICE)"]' \
		-target='aws_ecs_service.services["$(SERVICE)"]' \
		-target='aws_appautoscaling_target.ecs["$(SERVICE)"]' \
		-target='aws_appautoscaling_policy.cpu["$(SERVICE)"]'
	@printf "$(GREEN)✓ $(SERVICE) provisionado.$(NC)\n"

.PHONY: service-destroy
service-destroy:
	@[ -n "$(SERVICE)" ] || (printf "$(RED)Erro: passe SERVICE=nome-do-servico$(NC)\n" && exit 1)
	@printf "$(RED)▶ Destruindo serviço: $(SERVICE)$(NC)\n"
	terraform destroy -auto-approve \
		-target='aws_appautoscaling_policy.cpu["$(SERVICE)"]' \
		-target='aws_appautoscaling_target.ecs["$(SERVICE)"]' \
		-target='aws_ecs_service.services["$(SERVICE)"]' \
		-target='aws_lb_listener_rule.services["$(SERVICE)"]' \
		-target='aws_lb_target_group.services["$(SERVICE)"]' \
		-target='aws_ecs_task_definition.services["$(SERVICE)"]' \
		-target='aws_cloudwatch_log_group.ecs["$(SERVICE)"]'
	@printf "$(GREEN)✓ $(SERVICE) destruído.$(NC)\n"

# ============================================================
# UTILITÁRIOS
# ============================================================

.PHONY: status
status:
	@printf "$(GREEN)▶ Status dos serviços ECS no cluster $(CLUSTER):$(NC)\n"
	@aws ecs list-services \
		--cluster $(CLUSTER) \
		--region $(REGION) \
		--output text \
		--query 'serviceArns[*]' \
	| tr '\t' '\n' \
	| xargs -r -n10 aws ecs describe-services \
		--cluster $(CLUSTER) \
		--region $(REGION) \
		--output json \
		--services \
	| jq -r '.services[] | "  \(.serviceName | ltrimstr("dijkfood-")): running=\(.runningCount) desired=\(.desiredCount) [\(.status)]"' \
	| sort

.PHONY: logs
logs:
	@[ -n "$(SERVICE)" ] || (printf "$(RED)Erro: passe SERVICE=nome-do-servico$(NC)\n" && exit 1)
	@printf "$(GREEN)▶ Logs de /ecs/$(PROJECT)/$(SERVICE) (Ctrl+C para sair):$(NC)\n"
	aws logs tail /ecs/$(PROJECT)/$(SERVICE) --follow --region $(REGION)

.PHONY: frontend
frontend:
	@printf "$(GREEN)▶ Fazendo upload do frontend para o S3...$(NC)\n"
	$(eval FRONTEND_BUCKET := $(shell terraform output -raw s3_frontend_bucket 2>/dev/null))
	$(eval ALB_URL := $(shell terraform output -raw alb_dns_name 2>/dev/null))
	@[ -n "$(FRONTEND_BUCKET)" ] || (printf "$(RED)Erro: rode 'make up' primeiro$(NC)\n" && exit 1)
	@[ -n "$(ALB_URL)" ] || (printf "$(RED)Erro: ALB não encontrado no terraform output$(NC)\n" && exit 1)
	@printf "  API_BASE → $(ALB_URL)\n"
	@printf 'window.API_BASE = "%s";\n' "$(ALB_URL)" > ../frontend/config.js
	aws s3 sync ../frontend/ s3://$(FRONTEND_BUCKET)/ \
		--region $(REGION) \
		--delete \
		--cache-control "no-cache"
	@printf "$(GREEN)✓ Frontend publicado em: $(NC)"
	@terraform output -raw frontend_url 2>/dev/null
	@printf "\n"

.PHONY: schema
schema:
	@printf "$(GREEN)▶ Executando schema.sql no RDS via task Fargate temporária...$(NC)\n"
	@bash ../scripts/run_schema.sh

.PHONY: outputs
outputs:
	@printf "$(GREEN)▶ Outputs do Terraform:$(NC)\n"
	terraform output
