#!/usr/bin/env python3
"""
DijkFood A2 — Deploy Script (Automação Completa)

Cria toda a infraestrutura AWS, faz build/push dos containers,
executa seed data, roda o simulador de carga, coleta resultados e destrói tudo.

Uso:
    python deploy.py create     — Cria toda a infraestrutura
    python deploy.py destroy    — Destrói toda a infraestrutura
    python deploy.py full       — Cria, roda simulador, destrói

ATENÇÃO: Usa duas configurações AWS:
    - Profile 'default' (lab): todos os recursos
    - Profile 'bedrock': chamadas ao Amazon Bedrock
    - Todas as roles usam 'LabRole'
"""
import argparse
import json
import logging
import os
import sys
import time

import boto3

# Adicionar diretório pai ao path
sys.path.insert(0, os.path.dirname(__file__))

from infra.vpc import create_vpc, destroy_vpc
from infra.database import (
    create_rds, init_rds_schema, create_dynamodb_tables,
    destroy_rds, destroy_dynamodb_tables, get_lab_role_arn
)
from infra.streaming import (
    create_kinesis_stream, create_firehose,
    create_lambda_anomaly_detector, create_lambda_firehose_transform,
    destroy_streaming
)
from infra.analytics import (
    create_s3_buckets, create_glue_resources,
    create_cloudfront_distribution, destroy_analytics
)
from infra.compute import (
    create_ecr_repos, build_and_push_images, create_ecs_cluster,
    create_alb, create_task_definitions, create_ecs_services,
    create_auto_scaling, wait_services_stable, destroy_compute
)
from infra.monitoring import (
    create_log_groups, create_alarms, create_dashboard, destroy_monitoring
)

def upload_frontend(s3_client, bucket_name, alb_dns):
    """Faz upload dos arquivos do frontend para o S3 e configura a URL da API."""
    logger.info("=== Upload do Frontend para S3 ===")
    
    frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
    
    try:
        # Upload index.html
        s3_client.upload_file(
            os.path.join(frontend_dir, "index.html"),
            bucket_name,
            "index.html",
            ExtraArgs={"ContentType": "text/html"}
        )
        
        # Upload styles.css
        s3_client.upload_file(
            os.path.join(frontend_dir, "styles.css"),
            bucket_name,
            "styles.css",
            ExtraArgs={"ContentType": "text/css"}
        )
        
        # Configurar e fazer upload do app.js
        app_js_path = os.path.join(frontend_dir, "app.js")
        with open(app_js_path, "r", encoding="utf-8") as f:
            app_js_content = f.read()
            
        # Substituir API_BASE
        app_js_content = app_js_content.replace(
            "const API_BASE = window.location.origin;", 
            f'const API_BASE = "http://{alb_dns}";'
        )
        
        # Salvar em temp e subir
        temp_js = os.path.join(frontend_dir, "app_temp.js")
        with open(temp_js, "w", encoding="utf-8") as f:
            f.write(app_js_content)
            
        s3_client.upload_file(
            temp_js,
            bucket_name,
            "app.js",
            ExtraArgs={"ContentType": "application/javascript"}
        )
        
        os.remove(temp_js)
        logger.info(f"Frontend configurado para usar a API: http://{alb_dns}")
        logger.info("Upload de arquivos do frontend concluído com sucesso")
    except Exception as e:
        logger.error(f"Erro ao fazer upload do frontend: {e}")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("deploy.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger("deploy")

# Arquivo para persistir estado entre operações
STATE_FILE = "deploy_state.json"


def save_state(state):
    """Salva o estado da infraestrutura em arquivo."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)
    logger.info(f"Estado salvo em {STATE_FILE}")


def load_state():
    """Carrega o estado da infraestrutura."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def get_account_id():
    """Obtém o ID da conta AWS."""
    sts = boto3.client("sts")
    return sts.get_caller_identity()["Account"]


def create_all():
    """Cria toda a infraestrutura na ordem correta."""
    logger.info("=" * 80)
    logger.info("   DijkFood A2 — CRIAÇÃO DE INFRAESTRUTURA COMPLETA")
    logger.info("=" * 80)

    state = {}
    start_time = time.time()

    try:
        # Clientes boto3 (profile default = lab)
        ec2 = boto3.client("ec2", region_name="us-east-1")
        rds = boto3.client("rds", region_name="us-east-1")
        dynamodb = boto3.client("dynamodb", region_name="us-east-1")
        s3 = boto3.client("s3", region_name="us-east-1")
        kinesis = boto3.client("kinesis", region_name="us-east-1")
        firehose = boto3.client("firehose", region_name="us-east-1")
        lambda_client = boto3.client("lambda", region_name="us-east-1")
        glue = boto3.client("glue", region_name="us-east-1")
        ecs = boto3.client("ecs", region_name="us-east-1")
        ecr = boto3.client("ecr", region_name="us-east-1")
        elbv2 = boto3.client("elbv2", region_name="us-east-1")
        aas = boto3.client("application-autoscaling", region_name="us-east-1")
        cw = boto3.client("cloudwatch", region_name="us-east-1")
        logs = boto3.client("logs", region_name="us-east-1")
        cf = boto3.client("cloudfront", region_name="us-east-1")
        iam = boto3.client("iam", region_name="us-east-1")

        account_id = get_account_id()
        lab_role_arn = get_lab_role_arn(iam)
        logger.info(f"Conta AWS: {account_id}")
        logger.info(f"LabRole ARN: {lab_role_arn}")

        # ===== FASE 1: Rede =====
        logger.info("\n🔧 FASE 1: Rede (VPC, Subnets, SGs)")
        vpc_config = create_vpc(ec2)
        state["vpc"] = vpc_config
        save_state(state)

        # ===== FASE 2: Bancos de Dados =====
        logger.info("\n🔧 FASE 2: Bancos de Dados (RDS, DynamoDB)")
        db_config = create_rds(rds, vpc_config)
        state["db"] = db_config
        save_state(state)

        init_rds_schema(db_config)

        dynamodb_tables = create_dynamodb_tables(dynamodb)
        state["dynamodb_tables"] = dynamodb_tables
        save_state(state)

        # ===== FASE 3: Storage & Analytics =====
        logger.info("\n🔧 FASE 3: S3, Glue, Athena")
        s3_buckets = create_s3_buckets(s3, account_id)
        state["s3_buckets"] = s3_buckets
        save_state(state)

        glue_config = create_glue_resources(glue, s3_buckets, lab_role_arn)
        state["glue"] = glue_config
        save_state(state)

        # ===== FASE 4: Streaming =====
        logger.info("\n🔧 FASE 4: Kinesis, Firehose, Lambda")
        stream_arn = create_kinesis_stream(kinesis)
        state["stream_arn"] = stream_arn
        save_state(state)

        create_firehose(firehose, stream_arn, s3_buckets["data_lake"], lab_role_arn)
        create_lambda_anomaly_detector(lambda_client, stream_arn, lab_role_arn)
        create_lambda_firehose_transform(lambda_client, lab_role_arn)
        save_state(state)

        # ===== FASE 5: Monitoramento =====
        logger.info("\n🔧 FASE 5: CloudWatch Monitoring")
        create_log_groups(logs)

        # ===== FASE 6: Containers =====
        logger.info("\n🔧 FASE 6: ECR, Docker Build & Push")
        repos = create_ecr_repos(ecr)
        state["ecr_repos"] = repos
        save_state(state)

        build_and_push_images(ecr, repos, account_id)

        # ===== FASE 7: ECS =====
        logger.info("\n🔧 FASE 7: ECS Cluster, Task Definitions, ALB, Services")
        cluster_arn = create_ecs_cluster(ecs)
        state["cluster_arn"] = cluster_arn
        save_state(state)

        alb_config = create_alb(elbv2, vpc_config)
        state["alb"] = alb_config
        save_state(state)

        task_defs = create_task_definitions(ecs, repos, lab_role_arn, db_config)
        state["task_defs"] = task_defs

        services = create_ecs_services(ecs, cluster_arn, task_defs, alb_config["target_groups"], vpc_config)
        state["services"] = services
        save_state(state)

        # ===== FASE 8: Auto Scaling =====
        logger.info("\n🔧 FASE 8: Auto Scaling")
        create_auto_scaling(aas, cluster_arn, services)

        # ===== FASE 9: Alarmes e Dashboard =====
        logger.info("\n🔧 FASE 9: Alarmes e Dashboard CloudWatch")
        create_alarms(cw, alb_config["alb_arn"])
        create_dashboard(cw, alb_config["alb_arn"])

        # ===== FASE 10: CloudFront =====
        logger.info("\n🔧 FASE 10: CloudFront (Camada de Apresentação)")
        try:
            cf_config = create_cloudfront_distribution(cf, s3_buckets["frontend"])
        except Exception as e:
            logger.warning(f"CloudFront indisponível (LabRole sem permissão): {e}")
            cf_config = None
        state["cloudfront"] = cf_config
        save_state(state)
        
        # ===== FASE 10.5: Upload Frontend =====
        upload_frontend(s3, s3_buckets["frontend"], alb_config["alb_dns"])

        # ===== FASE 11: Estabilização =====
        logger.info("\n⏳ FASE 11: Aguardando estabilização dos serviços...")
        wait_services_stable(ecs, cluster_arn)

        # ===== RESULTADO =====
        elapsed = time.time() - start_time
        s3_website_url = f"http://{s3_buckets['frontend']}.s3-website-us-east-1.amazonaws.com"
        logger.info("\n" + "=" * 80)
        logger.info("   ✅ INFRAESTRUTURA CRIADA COM SUCESSO!")
        logger.info(f"   Tempo total: {elapsed/60:.1f} minutos")
        logger.info(f"   ALB URL: http://{alb_config['alb_dns']}")
        if cf_config:
            logger.info(f"   CloudFront URL: https://{cf_config['domain_name']}")
        else:
            logger.info(f"   Frontend (S3 Website): {s3_website_url}")
            logger.info("   (CloudFront não disponível — LabRole sem permissão)")
        logger.info("=" * 80)

        save_state(state)
        return state

    except Exception as e:
        logger.error(f"ERRO FATAL durante criação: {e}", exc_info=True)
        save_state(state)
        raise


def destroy_all():
    """Destrói toda a infraestrutura na ordem inversa."""
    logger.info("=" * 80)
    logger.info("   DijkFood A2 — DESTRUIÇÃO DE INFRAESTRUTURA")
    logger.info("=" * 80)

    state = load_state()
    if not state:
        logger.warning("Nenhum estado encontrado. Nada para destruir.")
        return

    start_time = time.time()

    try:
        ec2 = boto3.client("ec2", region_name="us-east-1")
        rds_client = boto3.client("rds", region_name="us-east-1")
        dynamodb = boto3.client("dynamodb", region_name="us-east-1")
        s3 = boto3.client("s3", region_name="us-east-1")
        kinesis = boto3.client("kinesis", region_name="us-east-1")
        firehose = boto3.client("firehose", region_name="us-east-1")
        lambda_client = boto3.client("lambda", region_name="us-east-1")
        glue = boto3.client("glue", region_name="us-east-1")
        ecs = boto3.client("ecs", region_name="us-east-1")
        ecr = boto3.client("ecr", region_name="us-east-1")
        elbv2 = boto3.client("elbv2", region_name="us-east-1")
        aas = boto3.client("application-autoscaling", region_name="us-east-1")
        cw = boto3.client("cloudwatch", region_name="us-east-1")
        logs = boto3.client("logs", region_name="us-east-1")
        cf = boto3.client("cloudfront", region_name="us-east-1")

        # Ordem inversa de criação
        logger.info("\n🗑️  FASE 1: Auto Scaling, ECS Services, ALB, ECR")
        destroy_compute(ecs, elbv2, ecr, aas, state.get("alb"), state.get("cluster_arn"))

        logger.info("\n🗑️  FASE 2: CloudWatch")
        destroy_monitoring(cw, logs)

        logger.info("\n🗑️  FASE 3: Streaming (Lambda, Firehose, Kinesis)")
        destroy_streaming(kinesis, firehose, lambda_client)

        logger.info("\n🗑️  FASE 4: Analytics (CloudFront, Glue, S3)")
        destroy_analytics(s3, glue, cf, state.get("s3_buckets", {}), state.get("cloudfront"))

        logger.info("\n🗑️  FASE 5: Bancos de Dados")
        destroy_rds(rds_client)
        destroy_dynamodb_tables(dynamodb)

        logger.info("\n🗑️  FASE 6: Rede (VPC)")
        if state.get("vpc"):
            destroy_vpc(ec2, state["vpc"])

        elapsed = time.time() - start_time
        logger.info("\n" + "=" * 80)
        logger.info(f"   ✅ INFRAESTRUTURA DESTRUÍDA COM SUCESSO!")
        logger.info(f"   Tempo total: {elapsed/60:.1f} minutos")
        logger.info("=" * 80)

        # Remover arquivo de estado
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)

    except Exception as e:
        logger.error(f"ERRO durante destruição: {e}", exc_info=True)
        raise


def run_full():
    """Executa fluxo completo: criar, semear, simular, coletar, destruir."""
    try:
        state = create_all()

        alb_url = f"http://{state['alb']['alb_dns']}"
        logger.info(f"\n📊 Executando simulador contra {alb_url}")

        # Executar simulador
        import subprocess
        subprocess.run([
            sys.executable, "simulator.py",
            "--url", alb_url,
            "--scenario", "normal",
            "--duration", "120",
        ], check=False)

        logger.info("Simulação completa. Destruindo infraestrutura...")
        time.sleep(30)  # aguardar dados fluírem para S3

    finally:
        destroy_all()


def main():
    parser = argparse.ArgumentParser(description="DijkFood A2 — Deploy Automation")
    parser.add_argument(
        "action",
        choices=["create", "destroy", "full"],
        help="Ação: create (criar infra), destroy (destruir infra), full (criar + simular + destruir)"
    )
    args = parser.parse_args()

    if args.action == "create":
        create_all()
    elif args.action == "destroy":
        destroy_all()
    elif args.action == "full":
        run_full()


if __name__ == "__main__":
    main()
