#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TERRAFORM_DIR="${REPO_ROOT}/terraform"
SQL_FILE="${REPO_ROOT}/sql/schema.sql"
REGION="${REGION:-us-east-1}"

echo "▶ Obtendo configurações do Terraform..."
cd "$TERRAFORM_DIR"

DB_HOST=$(terraform output -raw rds_endpoint)
DB_PASS=$(terraform output -raw db_password)
DB_USER=dijkfood
DB_NAME=dijkfood
S3_BUCKET=$(terraform output -raw s3_models_bucket)
SUBNET_ID=$(terraform output -json private_subnet_ids | jq -r '.[0]')
ECS_SG=$(terraform output -raw sg_ecs_id)
CLUSTER=$(terraform output -raw ecs_cluster_name)

ROLE_ARN=$(aws iam get-role --role-name LabRole --query 'Role.Arn' --output text --region "$REGION")

echo "▶ Fazendo upload do schema.sql para S3..."
S3_KEY="tmp/schema-runner/schema.sql"
aws s3 cp "$SQL_FILE" "s3://${S3_BUCKET}/${S3_KEY}" --region "$REGION"

echo "▶ Gerando URL pré-assinada (válida por 1h)..."
PRESIGNED_URL=$(aws s3 presign "s3://${S3_BUCKET}/${S3_KEY}" --expires-in 3600 --region "$REGION")

echo "▶ Registrando task definition temporária..."
CONTAINER_DEF_FILE=$(mktemp /tmp/schema-runner-XXXXXX.json)
cat > "$CONTAINER_DEF_FILE" << EOF
[{
  "name": "psql",
  "image": "postgres:16-alpine",
  "essential": true,
  "command": ["sh", "-c", "wget -qO /tmp/schema.sql '${PRESIGNED_URL}' && PGPASSWORD='${DB_PASS}' psql -h ${DB_HOST} -U ${DB_USER} -d ${DB_NAME} -f /tmp/schema.sql"],
  "logConfiguration": {
    "logDriver": "awslogs",
    "options": {
      "awslogs-group": "/ecs/dijkfood/schema-runner",
      "awslogs-region": "${REGION}",
      "awslogs-stream-prefix": "ecs",
      "awslogs-create-group": "true"
    }
  }
}]
EOF

TASK_DEF_ARN=$(aws ecs register-task-definition \
  --family dijkfood-schema-runner \
  --network-mode awsvpc \
  --requires-compatibilities FARGATE \
  --cpu 256 \
  --memory 512 \
  --execution-role-arn "$ROLE_ARN" \
  --task-role-arn "$ROLE_ARN" \
  --container-definitions "file://${CONTAINER_DEF_FILE}" \
  --region "$REGION" \
  --query 'taskDefinition.taskDefinitionArn' \
  --output text)

rm -f "$CONTAINER_DEF_FILE"

echo "▶ Iniciando task Fargate..."
TASK_ARN=$(aws ecs run-task \
  --cluster "$CLUSTER" \
  --task-definition "$TASK_DEF_ARN" \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[${SUBNET_ID}],securityGroups=[${ECS_SG}],assignPublicIp=DISABLED}" \
  --region "$REGION" \
  --query 'tasks[0].taskArn' \
  --output text)

echo "  Task: $TASK_ARN"
echo "▶ Aguardando conclusão (pode levar ~1-2 min para pull da imagem)..."

aws ecs wait tasks-stopped \
  --cluster "$CLUSTER" \
  --tasks "$TASK_ARN" \
  --region "$REGION"

EXIT_CODE=$(aws ecs describe-tasks \
  --cluster "$CLUSTER" \
  --tasks "$TASK_ARN" \
  --region "$REGION" \
  --query 'tasks[0].containers[0].exitCode' \
  --output text)

echo "▶ Limpando recursos temporários..."
aws s3 rm "s3://${S3_BUCKET}/${S3_KEY}" --region "$REGION" 2>/dev/null || true
aws ecs deregister-task-definition --task-definition "$TASK_DEF_ARN" --region "$REGION" > /dev/null 2>&1 || true

if [ "$EXIT_CODE" = "0" ]; then
  echo "✓ Schema aplicado com sucesso!"
else
  echo "✗ Erro ao aplicar schema (exit code: $EXIT_CODE)"
  echo "  Ver logs: aws logs tail /ecs/dijkfood/schema-runner --region $REGION"
  exit 1
fi
