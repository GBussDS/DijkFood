# DijkFood A2 — Plataforma de Delivery AI-Driven na Nuvem

Este projeto implementa uma arquitetura baseada em microsserviços orientada a eventos para uma plataforma de delivery fictícia, a **DijkFood**, com suporte nativo a Inteligência Artificial via Amazon Bedrock e análise de dados em tempo real.

O projeto provisiona toda a infraestrutura na AWS automaticamente utilizando Python (`boto3`), abrangendo VPCs, ECS Fargate, RDS PostgreSQL, DynamoDB, Kinesis, Firehose, S3 Data Lake, Glue, Athena, e CloudFront.

---

## 📋 Pré-requisitos

Antes de iniciar o deploy, você precisará ter instalado em sua máquina:

1. **[AWS CLI v2](https://aws.amazon.com/cli/)** — Interface de linha de comando da AWS.
2. **[Python 3.12+](https://www.python.org/downloads/)** — Para executar os scripts de automação.
3. **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** — Necessário rodando em background para que o script possa fazer o build das imagens e enviá-las para o ECR.

---

## 🔐 Configuração das Credenciais AWS (Dual Profile)

A arquitetura deste projeto exige o uso de **dois perfis da AWS**, pois ele foi projetado para rodar os serviços principais em uma conta educacional (AWS Academy / Learner Lab) que possui a permissão `LabRole`, mas o serviço de IA Generativa (Amazon Bedrock) não está disponível no AWS Academy e precisa de uma conta pessoal secundária.

### Passo 1: Configurar a conta principal (AWS Academy / Lab)

Esta conta hospedará 99% da infraestrutura (VPC, ECS, RDS, Kinesis, S3, etc).

1. Entre no seu AWS Academy / Learner Lab e clique em **AWS Details**.
2. Clique em **Show** ao lado de *AWS CLI*.
3. Copie as credenciais fornecidas (vão se parecer com as abaixo).
4. Abra o arquivo `~/.aws/credentials` (no Windows: `C:\Users\SEU_USUARIO\.aws\credentials`) usando o bloco de notas.
5. Cole as credenciais sob o perfil `[default]`:

```ini
[default]
aws_access_key_id=ASIA...
aws_secret_access_key=...
aws_session_token=...
```

*Nota: As credenciais do AWS Academy expiram a cada algumas horas. Lembre-se de atualizá-las antes de rodar o deploy se a sua sessão expirar.*

### Passo 2: Configurar a conta secundária (Amazon Bedrock)

Esta é sua conta AWS pessoal onde você habilitou o acesso aos modelos Claude no Amazon Bedrock.

1. No Console da sua conta pessoal, vá até **IAM** e crie um usuário com permissão de uso do Bedrock (ex: `AmazonBedrockFullAccess`).
2. Gere **Access Keys** (Access Key ID e Secret Access Key) para este usuário.
3. No mesmo arquivo `~/.aws/credentials`, adicione um **novo** perfil chamado `[bedrock]`:

```ini
[bedrock]
aws_access_key_id=AKIA...
aws_secret_access_key=...
```
*(Contas normais de IAM não precisam de `aws_session_token`)*

### Passo 3: Configurar a Região Padrão

Edite ou crie o arquivo `~/.aws/config` (no Windows: `C:\Users\SEU_USUARIO\.aws\config`):

```ini
[default]
region=us-east-1
output=json

[profile bedrock]
region=us-east-1
output=json
```

---

## 🚀 Como fazer o Deploy

Com os perfis da AWS devidamente configurados e o Docker Desktop rodando, abra o seu terminal (Prompt de Comando ou PowerShell) na pasta do projeto e siga os passos abaixo:

### 1. Instalar as dependências do script

O script `deploy.py` depende principalmente do `boto3` para orquestrar a nuvem:

```bash
pip install boto3
```

### 2. Rodar o Deploy de Criação

Execute o script de automação no modo `create`. Este processo leva de **15 a 25 minutos** pois irá criar as VPCs, provisionar um banco de dados Multi-AZ RDS, subir a stack analítica e fazer o push de 5 containers Docker para o AWS ECR.

```bash
python deploy.py create
```

**O que observar durante o deploy:**
- O script logará cada passo. Se encontrar um erro momentâneo (ex: eventual consistência da AWS), o script irá aguardar as disponibilidades automaticamente usando `waiters`.
- Ao final, ele imprimirá no console a URL do **Application Load Balancer (ALB)** e a URL do **CloudFront**.

### 3. Acessar a Aplicação

O CloudFront serve o conteúdo estático da pasta `frontend` em uma CDN global. **O script de deploy configura automaticamente a URL do backend e faz o upload dos arquivos HTML/JS/CSS para você.**

Basta acessar o link do **CloudFront URL** impresso no final do script (`https://d123456...cloudfront.net`) e o painel já estará operando com dados reais.

*(Você também pode rodar o frontend no seu computador simplesmente dando dois cliques no arquivo `index.html` na pasta `frontend`, lembrando que ele fará requests diretos ao ALB hospedado na AWS caso você não modifique manualmente a variável `API_BASE`).*

---

## 📊 Rodando o Simulador de Carga

Para ver o dashboard ganhando vida e o Kinesis processando dados analíticos em tempo real, use o script de simulação. Instale os requisitos dele primeiro:

```bash
pip install aiohttp pandas scikit-learn
```

Rode uma carga normal para encher o banco de dados e os streams:

```bash
python simulator.py --url http://SUA_URL_DO_ALB_AQUI --scenario normal --duration 300
```
Isso irá criar restaurantes e entregadores fakes e lançará dezenas de pedidos por segundo no ALB por 5 minutos, engatilhando os serviços de Tracking e o Fargate Auto Scaling.

---

## 🧠 Treinando Modelos de ML

Após rodar algumas simulações de carga para popular o seu *Data Lake* (os dados vão do Kinesis para o Firehose, para o Glue e caem no S3), você pode treinar e subir novos modelos de IA para previsões de tempo de entrega:

```bash
# Treinar com dados reais do Amazon Athena e salvar no bucket do projeto
python train_model.py --bucket NOME_DO_SEU_BUCKET_DE_MODELOS
```

*(Caso não queira esperar o Glue processar dados, você pode rodar `python train_model.py --synthetic` para gerar dados falsos de ML)*.

---

## 🗑️ Como Destruir e Evitar Cobranças

Como a infraestrutura provisiona RDS Multi-AZ, Load Balancers, CloudFront, e NAT Gateways, é **fundamental** destruir o laboratório ao terminar seus testes para não consumir todo o crédito do Learner Lab.

Basta rodar o comando inverso:

```bash
python deploy.py destroy
```

O script irá remover os Scale Targets, apagar as instâncias do ECS e os repositórios ECR de forma segura, apagar os Buckets S3 (esvaziando-os primeiro) e de-registrar a VPC, finalizando o encerramento do seu ambiente completo.

---

### Desenvolvimento Local (Alternativa)

Não quer ou não pode usar a AWS agora? Você pode rodar tudo no seu próprio computador usando o LocalStack (AWS local falsa).

```bash
docker-compose up --build
```
*Acesse o dashboard estático abrindo o `frontend/index.html` no seu navegador com a `API_BASE = "http://localhost:8000"` (ou as portas correspondentes de cada serviço).*
