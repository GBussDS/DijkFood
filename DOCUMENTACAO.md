# Documentação Detalhada da Arquitetura: DijkFood

Este documento descreve exaustivamente a arquitetura técnica da plataforma DijkFood. Cada serviço AWS utilizado é listado com seus **parâmetros exatos**, o **motivo da configuração** e o **fluxo de conexão** entre eles. Também são apresentadas as alternativas descartadas.

---

## 1. Topologia de Rede e Segurança (VPC & Security Groups)
**Arquivo de Infraestrutura:** `infra/vpc.py`

O coração da segurança da aplicação. Nenhum banco de dados ou backend possui acesso direto à internet pública.

### AWS VPC (Virtual Private Cloud)
* **Parâmetro:** CIDR Block `10.0.0.0/16`.
* **Subnets Públicas:** Duas subnets (`10.0.1.0/24` e `10.0.2.0/24`) com `MapPublicIpOnLaunch=True`. Ficam hospedadas em Zonas de Disponibilidade diferentes.
* **Subnets Privadas:** Duas subnets (`10.0.3.0/24` e `10.0.4.0/24`). Não possuem IPs públicos.
* **Motivo:** Isolar a camada de banco de dados e processamento (Privada) da camada de entrada de dados (Pública), garantindo compliance com boas práticas de segurança.
* **Descartado:** Usar a VPC Default da AWS (fere isolamento) ou instanciar tudo em Subnet Pública (risco de invasão direta).

### Roteamento Externo (IGW e NAT Gateway)
* **Parâmetros:**
  - 1x **Internet Gateway (IGW)** anexado diretamente na VPC e listado na Route Table das Subnets Públicas.
  - 1x **NAT Gateway** provisionado na Subnet Pública `10.0.1.0/24` com 1x Elastic IP. Listado na Route Table das Subnets Privadas.
* **Motivo:** O NAT Gateway permite que os containers do ECS na subnet privada façam download das imagens Docker no ECR e acessem o Amazon Bedrock (via internet), mas bloqueia requisições chegando de fora.

### Grupos de Segurança (Security Groups)
* **SG do ALB (Load Balancer):** Permite entrada (Ingress) TCP portas 80 e 443 de `0.0.0.0/0` (Toda a internet).
* **SG do ECS (Containers):** Permite entrada (Ingress) apenas das portas `8000 a 8005`, e **obrigatoriamente a origem deve ser o SG do ALB**.
* **SG do RDS (Banco de Dados):** Permite entrada (Ingress) apenas na porta `5432`, e **obrigatoriamente a origem deve ser o SG do ECS**.
* **Conexão:** O tráfego flui exatamente nesta ordem: Internet -> ALB -> ECS -> RDS.

---

## 2. Camada de Aplicação (ECS Fargate & ALB)
**Arquivo de Infraestrutura:** `infra/compute.py`

### Amazon Elastic Container Registry (ECR)
* **Parâmetros:** `scanOnPush=True` ativado.
* **Motivo:** Armazena as imagens Docker compiladas. O ScanOnPush garante que as imagens sejam inspecionadas em busca de vulnerabilidades antes do deploy.

### Application Load Balancer (ALB)
* **Parâmetros:** 
  - Scheme: `internet-facing` (voltado pra web).
  - Alocado nas Subnets Públicas.
  - Target Groups individuais para cada serviço e portas customizadas.
* **Conexão:** O frontend JS chama a URL pública do ALB. O ALB roteia a chamada com base no "Path Pattern":
  - `/api/orders*` -> Redirecionado para Target Group `order-processor` ou `order-management`.
  - `/api/positions*` -> Redirecionado para Target Group `position-tracker`.
  - `/api/chat*` -> Redirecionado para Target Group `conversational`.
* **Descartado:** API Gateway foi descartado pelo alto custo em APIs de volumetria constante (polling) em comparação ao ALB.

### Amazon ECS (Elastic Container Service) no modo Fargate
* **Parâmetros dos Containers (Tasks):**
  1. `order-processor` (Porta 8000 | CPU: 512 | RAM: 1024)
  2. `order-management` (Porta 8001 | CPU: 256 | RAM: 512)
  3. `position-tracker` (Porta 8002 | CPU: 256 | RAM: 512)
  4. `conversational` (Porta 8003 | CPU: 1024 | RAM: 2048) -> *Mais RAM pelo uso intensivo do LangChain.*
  5. `ml-inference` (Porta 8004 | CPU: 512 | RAM: 1024)
  6. `dashboard-analytics` (Porta 8005 | CPU: 512 | RAM: 1024)
* **Conexão & Variáveis de Ambiente Injetadas:** 
  - Todo container recebe as chaves do banco (`DB_HOST`, `DB_USER`), `KINESIS_STREAM` e AWS Credentials nativas via *LabRole*. 
* **Descartado:** Modo ECS-EC2. Fargate foi escolhido por ser 100% Serverless, sem necessidade de atualizar SO ou instâncias de máquina física.

---

## 3. Bancos de Dados Transacionais
**Arquivo de Infraestrutura:** `infra/db.py`

### Amazon RDS (PostgreSQL)
* **Parâmetros:**
  - Instância: `db.t3.micro`.
  - Engine: PostgreSQL `16.3`.
  - Storage: `20 GB` (gp2).
  - PubliclyAccessible: `False`.
  - Master Username: `dijkfood`.
* **Motivo:** Armazena clientes, restaurantes, roteamento e pedidos. Garante consistência ACID necessária em transações financeiras e de pedidos. 
* **Descartado:** Multi-AZ (descartado pelo alto custo para labs).

### Amazon DynamoDB
* **Parâmetros:**
  - Tabela: `courier_positions`.
  - Partition Key: `courier_id` (String).
  - BillingMode: `PAY_PER_REQUEST` (Sob Demanda).
* **Motivo:** Os entregadores enviam posições GPS a cada X segundos. O DynamoDB escala instantaneamente para suportar milhares de gravações/segundo sem sofrer *Row Locking* (algo que mataria o RDS).
* **Conexão:** Serviço `position-tracker` grava (PutItem) e `conversational` faz leitura rápida para informar ao cliente onde o entregador está.

---

## 4. Pipeline Analítico & Big Data
**Arquivos de Infraestrutura:** `infra/streaming.py` e `infra/analytics.py`

Esta é a camada que engloba o rastreio de eventos (Logs de pedidos, alterações de status, e mensagens de chat).

### Amazon Kinesis Data Streams
* **Parâmetros:** Stream Name: `dijkfood-events`. Shard Count: `1` (suficiente para 1000 requisições/seg). Retention: 24h.
* **Motivo:** Em vez do container travar para salvar um histórico no banco, ele simplesmente joga um evento JSON no Stream e segue a vida.

### Amazon Kinesis Data Firehose
* **Parâmetros:** 
  - Buffer Size: `5 MB`
  - Buffer Interval: `60 segundos`
  - Compressão: `GZIP` (formato original `JSON`)
* **Conexão:** Ele "assina" o Stream do Kinesis. A cada 60s, ele pega todos os milhares de eventos gerados em JSON, comprime com GZIP e empurra para o S3.
* **Motivo da mudança:** Originalmente, a ideia era usar o AWS Glue atrelado ao Firehose para converter os dados para `Parquet` (formato binário ultra-rápido). Porém, a AWS exige que o buffer mínimo para conversão de formato seja de 64MB. Para não atrasar os dados do nosso laboratório (que não gera 64MB de tráfego por minuto), o *fallback* desativou a conversão e está salvando em GZIP.
* **Descartado:** Funções AWS Lambda. Escrever manualmente do Stream pro S3 exigiria código extra e custaria mais caro com milhões de invocações.

### AWS Glue Data Catalog & Amazon Athena
* **Parâmetros do Glue:** Database `dijkfood_analytics`, Tabela `events` mapeada via SerDe `OpenXJsonSerDe` para ler nativamente os JSONs comprimidos em GZIP. Colunas tipadas (latitude, longitude, old_status, timestamp) e particionamento ativado por ano, mês, dia e hora.
* **Parâmetros do Athena:** Lê a tabela do Glue e escreve os resultados das queries (`ATHENA_OUTPUT`) em outro bucket chamado `dijkfood-athena-results`.
* **Conexão:** O serviço `dashboard-analytics` dispara `SELECTs` no Athena. O Athena vai fisicamente ler os arquivos GZIP/JSON lá no S3, calcular os totais e retornar via API.
* **Descartado:** Amazon Redshift foi descartado pois exigiria uma máquina (cluster) ligada eternamente gerando altos custos mensais. Athena cobra por Mb consultado (apenas centavos).

---

## 5. Front-End Serverless
**Arquivos:** `frontend/*` e `fix_s3.py`

### Amazon S3 (Static Website Hosting)
* **Parâmetros:**
  - Index Document: `index.html`.
  - Error Document: `index.html`.
  - Bucket Policy: `PublicReadGetObject` ativada para expor o HTML/CSS/JS.
  - Cors Rules: Ativadas para `*` aceitando métodos `GET, PUT, POST`.
* **Conexão:** O browser do usuário faz download direto do S3. A partir daí, o código Javascript dentro do navegador se comunica diretamente via rede com o **Application Load Balancer (ALB)** do passo 2.
* **Descartado:** O projeto original usava **Amazon CloudFront** na frente do S3, pois ele faz cache via CDN mundial e permite TLS (HTTPS gratuito com o ACM). Porém, ele foi substituído pelo S3 direto, pois as contas `LabRole` da AWS Academy banem permanentemente as contas caso tentem provisionar distribuições CloudFront.

---

## 6. Inteligência Artificial e Machine Learning
**Arquivos:** `services/conversational` e `services/ml-inference`

### Amazon Bedrock
* **Parâmetros:**
  - Modelo Utilizado: `amazon.nova-micro-v1:0` (Amazon Nova Micro).
  - Paradigma: `Tool Calling Agent` usando *LangChain*.
  - Temperatura: `0.1` (focado em análise determinística e respostas exatas).
* **Conexão:** O backend de Chat monta o histórico, recebe as credenciais do Bedrock localmente injetadas via código e dispara a API do Bedrock. Se o modelo perceber que precisa consultar o banco, ele aciona ferramentas do próprio Python internamente que vão até o RDS ou Athena, e então gera a resposta textual ao usuário.
* **Descartado:** `anthropic.claude-3-sonnet-20240229-v1:0` e `amazon.titan-text-express-v1`. O Titan não possui recursos maduros nativos para conectar com a função de *Tool Calling* do LangChain. Já o Claude 3 foi bloqueado pela AWS pois contas institucionais não possuem o formulário obrigatório de uso da Anthropic preenchido. O Amazon Nova Micro serviu de substituto nativo perfeito.

### Machine Learning Customizado
* **Conexão:** O serviço `order-processor` possui uma chamada HTTP simples de 5 segundos de *timeout* para o container de predição (`ml-inference`) assim que um novo pedido chega, visando anexar uma margem extra de minutos no cálculo matemático do Dijkstra.
* **Descartado:** Amazon SageMaker. Treinar e hospedar um endpoint no SageMaker tem custos proibitivos para um laboratório rápido. Optou-se por embarcar um arquivo Scikit-Learn local (`.pkl`) na própria imagem Docker do `ml-inference`.
