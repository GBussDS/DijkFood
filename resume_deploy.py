import json
import logging
import sys
import boto3
import os
import time

# Ensure we can import from deploy.py and infra.*
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from deploy import get_account_id, save_state, STATE_FILE, logger, upload_frontend
from infra.compute import build_and_push_images, create_ecs_cluster, create_task_definitions, create_ecs_services, create_auto_scaling, wait_services_stable, create_alb
from infra.monitoring import create_alarms, create_dashboard
from infra.analytics import create_cloudfront_distribution

def get_lab_role_arn(iam_client):
    try:
        role = iam_client.get_role(RoleName="LabRole")
        return role["Role"]["Arn"]
    except Exception as e:
        logger.error(f"Erro ao obter LabRole: {e}")
        raise

def resume_all():
    logger.info("Resuming deployment from Phase 6...")
    with open("deploy_state.json", "r") as f:
        state = json.load(f)
    
    account_id = get_account_id()
    
    # Initialize boto3 clients
    iam = boto3.client("iam", region_name="us-east-1")
    ecr = boto3.client("ecr", region_name="us-east-1")
    ecs = boto3.client("ecs", region_name="us-east-1")
    elbv2 = boto3.client("elbv2", region_name="us-east-1")
    aas = boto3.client("application-autoscaling", region_name="us-east-1")
    cw = boto3.client("cloudwatch", region_name="us-east-1")
    cf = boto3.client("cloudfront", region_name="us-east-1")
    s3 = boto3.client("s3", region_name="us-east-1")

    lab_role_arn = get_lab_role_arn(iam)
    
    repos = state["ecr_repos"]
    vpc_config = state["vpc"]
    db_config = state["db"]
    s3_buckets = state["s3_buckets"]
    
    # Retry Phase 6
    logger.info("\n\U0001f527 FASE 6: ECR, Docker Build & Push (SKIPPED)")
    # build_and_push_images(ecr, repos, account_id)

    # ===== FASE 7: ECS =====
    logger.info("\n\U0001f527 FASE 7: ECS Cluster, Task Definitions, ALB, Services")
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
    logger.info("\n\U0001f527 FASE 8: Auto Scaling")
    create_auto_scaling(aas, cluster_arn, services)

    # ===== FASE 9: Alarmes e Dashboard =====
    logger.info("\n\U0001f527 FASE 9: Alarmes e Dashboard CloudWatch")
    create_alarms(cw, alb_config["alb_arn"])
    create_dashboard(cw, alb_config["alb_arn"])

    # ===== FASE 10: CloudFront =====
    logger.info("\n\U0001f527 FASE 10: CloudFront (Camada de Apresentação)")
    try:
        cf_config = create_cloudfront_distribution(cf, s3_buckets["frontend"])
    except Exception as e:
        logger.warning(f"CloudFront indisponível (LabRole não tem permissão): {e}")
        cf_config = None
    state["cloudfront"] = cf_config
    save_state(state)
    
    # ===== FASE 10.5: Upload Frontend =====
    upload_frontend(s3, s3_buckets["frontend"], alb_config["alb_dns"])

    # ===== FASE 11: Estabilização =====
    logger.info("\n\u23f3 FASE 11: Aguardando estabilização dos serviços...")
    wait_services_stable(ecs, cluster_arn)

    # URL do frontend: CloudFront se disponível, senão S3 Website Hosting
    s3_website_url = f"http://{s3_buckets['frontend']}.s3-website-us-east-1.amazonaws.com"

    logger.info("\n" + "="*80)
    logger.info("\u2705 DEPLOY CONCLUÍDO COM SUCESSO!")
    logger.info("="*80)
    logger.info(f"\U0001f4cd ALB (Backend): http://{alb_config['alb_dns']}")
    if cf_config:
        logger.info(f"\U0001f4cd CloudFront (Frontend): https://{cf_config['domain_name']}")
    else:
        logger.info(f"\U0001f4cd Frontend (S3 Website): {s3_website_url}")
        logger.info("   (CloudFront não disponível — LabRole sem permissão)")
    logger.info("\nVocê já pode rodar o simulador:")
    logger.info(f"python simulator.py --url http://{alb_config['alb_dns']} --scenario normal --duration 120")

if __name__ == "__main__":
    resume_all()
