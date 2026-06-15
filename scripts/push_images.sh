#!/usr/bin/env bash
# push_images.sh — Build e push de imagens Docker para o Amazon ECR.
# Uso: bash scripts/push_images.sh [nome-do-servico]

set -e

REGION="${AWS_REGION:-us-east-1}"
PROJECT="${PROJECT:-dijkfood}"
SINGLE_SERVICE="${1:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

ALL_SERVICES=(
  order-processor
  order-management
  position-tracker
  conversational
  ml-inference
  dashboard-analytics
)

# ── Account ID e registry URL (formato fixo do ECR) ──────────────────────────

echo "▶ Obtendo Account ID..."
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "  Account: $ACCOUNT_ID | Região: $REGION"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# ── Login no ECR com captura explícita de erros ───────────────────────────────

echo "▶ Obtendo token ECR..."
ECR_TOKEN_FILE=$(mktemp)
ECR_ERR_FILE=$(mktemp)

set +e
aws ecr get-login-password --region "$REGION" \
  >"$ECR_TOKEN_FILE" \
  2>"$ECR_ERR_FILE"
ECR_EXIT=$?
set -e

if [ $ECR_EXIT -ne 0 ] || [ ! -s "$ECR_TOKEN_FILE" ]; then
  echo ""
  echo "ERRO: falha ao obter token do ECR (exit $ECR_EXIT)" >&2
  echo "Mensagem AWS:" >&2
  cat "$ECR_ERR_FILE" >&2
  echo "" >&2
  echo "Causas comuns:" >&2
  echo "  - Credenciais do AWS Academy expiradas → atualize ~/.aws/credentials" >&2
  echo "  - Falta permissão ecr:GetAuthorizationToken na role" >&2
  rm -f "$ECR_TOKEN_FILE" "$ECR_ERR_FILE"
  exit 1
fi

echo "▶ Autenticando no Docker..."
cat "$ECR_TOKEN_FILE" | docker login --username AWS --password-stdin "$REGISTRY"
LOGIN_EXIT=$?
rm -f "$ECR_TOKEN_FILE" "$ECR_ERR_FILE"
if [ $LOGIN_EXIT -ne 0 ]; then
  echo "ERRO: docker login falhou (exit $LOGIN_EXIT)" >&2
  echo "Verifique se o Docker está rodando e se as credenciais AWS são válidas." >&2
  exit 1
fi
echo "✓ Login no ECR realizado"

# ── Build e Push ──────────────────────────────────────────────────────────────

build_and_push() {
  local svc="$1"
  local svc_dir="$REPO_ROOT/services/$svc"
  local image="${REGISTRY}/${PROJECT}/${svc}:latest"

  if [ ! -d "$svc_dir" ]; then
    echo "Aviso: '$svc_dir' não encontrado, pulando." >&2
    return
  fi

  echo ""
  echo "▶ [$svc] Building (linux/amd64)..."
  docker buildx build --platform linux/amd64 -t "$image" "$svc_dir"

  echo "▶ [$svc] Pushing..."
  docker push "$image"

  echo "✓ [$svc] enviado: $image"
}

if [ -n "$SINGLE_SERVICE" ]; then
  build_and_push "$SINGLE_SERVICE"
else
  for svc in "${ALL_SERVICES[@]}"; do
    build_and_push "$svc"
  done
fi

echo ""
echo "✓ Push concluído. Rode 'make status' para verificar os containers."
