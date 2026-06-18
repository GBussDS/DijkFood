# DijkFood A2

Plataforma de delivery com arquitetura na AWS. Toda a infraestrutura é gerenciada por **Terraform** e os comandos do dia a dia são expostos via **Makefile**

Serviços AWS provisionados automaticamente: VPC, ECS Fargate, RDS PostgreSQL, DynamoDB, Kinesis Data Streams, Kinesis Firehose, S3 Data Lake, Glue, Athena, ECR e Application Load Balancer

---

## Pré-requisitos

| Ferramenta | Versão mínima |
|---|---|
| [AWS CLI v2](https://aws.amazon.com/cli/) | 2.x |
| [Terraform](https://www.terraform.io/downloads) | 1.5+ |
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | qualquer |
| [jq](https://stedolan.github.io/jq/) | qualquer |
| Python 3.12+ | — |

---

## Configuração das Credenciais AWS

O projeto exige **dois perfis AWS** porque o Amazon Bedrock não está disponível em contas do AWS Academy e precisa de uma conta pessoal separada

### Conta principal - AWS Academy (Learner Lab)

Hospeda 99% da infraestrutura (VPC, ECS, RDS, Kinesis, S3, etc.)

1. No Learner Lab, clique em **AWS Details -> Show** ao lado de *AWS CLI*
2. Abra `~/.aws/credentials` e cole sob o perfil `[default]`:

```ini
[default]
aws_access_key_id=ASIA...
aws_secret_access_key=...
aws_session_token=...
```

### Conta secundária - Amazon Bedrock

1. Na conta que possui o Bedrock, crie um usuário IAM com a policy `AmazonBedrockFullAccess` e gere Access Keys.
2. Adicione ao mesmo arquivo `~/.aws/credentials`:

```ini
[bedrock]
aws_access_key_id=AKIA...
aws_secret_access_key=...
```

### Arquivo `~/.aws/config`

```ini
[default]
region=us-east-1
output=json

[profile bedrock]
region=us-east-1
output=json
```

---

## Deploy - Passo a Passo

### 1. Configure as variáveis

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
```

Edite `terraform.tfvars` com sua senha do banco e as credenciais do Bedrock:

```hcl
db_password               = "SUA_SENHA_AQUI"
bedrock_access_key_id     = "AKIA..."
bedrock_secret_access_key = "..."
```

### 2. Suba toda a infraestrutura

```bash
make up
```

Isso cria VPC, subnets, SGs, RDS, DynamoDB, S3, Kinesis, Glue, ECR, ECS Cluster e ALB  
Leva **15-25 minutos**, principalmente pelo RDS

### 3. Build e push das imagens Docker

```bash
make push
```

Compila os 6 containers e envia para o ECR (Docker Desktop precisa estar rodando)

### 4. Execute o schema no banco

```bash
make schema
```

Cria as tabelas PostgreSQL (`customers`, `restaurants`, `couriers`, `orders`, `order_events`)

### 5. Acesse a aplicação

```bash
make outputs
```

A saída mostrará:
- `alb_dns_name` - URL do backend (todos os microsserviços)
- `frontend_url` - URL do painel estático hospedado no S3

---

## Destruir a Infraestrutura

Para destruir toda a infraestrutura e evitar gastos:

```bash
make down
```

Remove todos os recursos provisionados pelo Terraform (ECS, RDS, Load Balancer, S3, Kinesis, etc.)

---

## Gerenciamento Individual de Serviços

### Parar e retomar containers (instantâneo, sem Terraform)

Esses comandos alteram apenas o `desired_count` do ECS, são os mais rápidos para economizar recursos durante desenvolvimento:

```bash
# Para um container específico (desired_count = 0)
make service-stop SERVICE=ml-inference

# Retoma (desired_count = min_tasks configurado)
make service-start SERVICE=order-processor

# Para e reinicia (útil para forçar novo deploy após push)
make service-restart SERVICE=conversational
```

### Criar e destruir um serviço individualmente (via Terraform)

Útil quando você quer remover completamente um serviço (Target Group, Listener Rule, Auto Scaling) sem derrubar o resto:

```bash
# Provisiona apenas o serviço especificado
make service-create SERVICE=dashboard-analytics

# Remove apenas o serviço especificado
make service-destroy SERVICE=dashboard-analytics
```

Você também pode controlar quais serviços existem editando `terraform.tfvars`:

```hcl
# Para remover o serviço conversational do provisionamento:
enabled_services = [
  "order-processor",
  "order-management",
  "position-tracker",
  "ml-inference",
  "dashboard-analytics",
  # "conversational",  <- comentado = será destruído no próximo apply
]
```

Depois:

```bash
make plan    # confirme o que será removido
make up      # aplica a mudança
```

### Ver status e logs

```bash
# Status de todos os serviços (running / desired / status)
make status

# Tail dos logs em tempo real
make logs SERVICE=order-processor
```

---

## Referência de Comandos

| Comando | O que faz |
|---|---|
| `make up` | Cria toda a infraestrutura |
| `make down` | Destrói toda a infraestrutura |
| `make plan` | Mostra as mudanças antes de aplicar |
| `make push` | Build + push de todas as imagens |
| `make push SERVICE=x` | Build + push de uma única imagem |
| `make service-stop SERVICE=x` | Para um container (desired=0) |
| `make service-start SERVICE=x` | Retoma um container |
| `make service-restart SERVICE=x` | Para e reinicia um container |
| `make service-create SERVICE=x` | Provisiona um serviço via Terraform |
| `make service-destroy SERVICE=x` | Destrói um serviço via Terraform |
| `make status` | Status de todos os containers ECS |
| `make logs SERVICE=x` | Tail dos logs CloudWatch |
| `make schema` | Executa o schema SQL no RDS |
| `make outputs` | Mostra todas as URLs e IDs |

---

## Simulador de Carga

Para popular o banco e o Kinesis com dados reais:

```bash
pip install aiohttp pandas scikit-learn
python scripts/load_test.py --url http://SEU_ALB_DNS --scenario normal --duration 300
```

Cria restaurantes e entregadores fictícios e dispara dezenas de pedidos por segundo durante 5 minutos.

---

## Treinamento de Modelos ML

Após rodar o simulador (para ter dados no Data Lake), treine modelos reais:

```bash
# Com dados reais do Athena
python train_model.py --bucket $(cd terraform && terraform output -raw s3_models_bucket)

# Com dados sintéticos (sem precisar esperar o Glue processar)
python train_model.py --synthetic
```

---

## Desenvolvimento Local (sem AWS)

Para rodar tudo localmente com LocalStack:

```bash
docker-compose up --build
```

Abra `frontend/index.html` no navegador. A variável `API_BASE` no `frontend/app.js` deve apontar para `http://localhost:8000`.
