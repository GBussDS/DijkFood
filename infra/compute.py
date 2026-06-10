"""
DijkFood — Infrastructure: ECR, ECS Cluster, Task Definitions, ALB, Services, Auto Scaling
"""
import json
import logging
import os
import subprocess
import time

import boto3

logger = logging.getLogger(__name__)

IAM_ROLE = "LabRole"
REGION = "us-east-1"

# Definição dos microsserviços
SERVICES = [
    {
        "name": "order-processor",
        "port": 8000,
        "cpu": "1024",
        "memory": "4096",
        "path_patterns": ["/api/orders*"],
        "priority": 1,
        "min_tasks": 2,
        "max_tasks": 10,
        "scaling_target": 70,
    },
    {
        "name": "order-management",
        "port": 8001,
        "cpu": "512",
        "memory": "1024",
        "path_patterns": ["/api/customers*", "/api/restaurants*", "/api/couriers*"],
        "priority": 2,
        "min_tasks": 2,
        "max_tasks": 8,
        "scaling_target": 70,
    },
    {
        "name": "position-tracker",
        "port": 8002,
        "cpu": "512",
        "memory": "1024",
        "path_patterns": ["/api/positions*"],
        "priority": 3,
        "min_tasks": 2,
        "max_tasks": 15,
        "scaling_target": 60,
    },
    {
        "name": "conversational",
        "port": 8003,
        "cpu": "1024",
        "memory": "2048",
        "path_patterns": ["/api/chat*"],
        "priority": 4,
        "min_tasks": 1,
        "max_tasks": 4,
        "scaling_target": 70,
    },
    {
        "name": "ml-inference",
        "port": 8004,
        "cpu": "512",
        "memory": "1024",
        "path_patterns": ["/api/predictions*"],
        "priority": 5,
        "min_tasks": 1,
        "max_tasks": 4,
        "scaling_target": 70,
    },
    {
        "name": "dashboard-analytics",
        "port": 8005,
        "cpu": "512",
        "memory": "1024",
        "path_patterns": ["/api/dashboard*"],
        "priority": 6,
        "min_tasks": 1,
        "max_tasks": 4,
        "scaling_target": 70,
    },
]


def get_lab_role_arn(iam_client):
    role = iam_client.get_role(RoleName=IAM_ROLE)
    return role["Role"]["Arn"]


def create_ecr_repos(ecr_client):
    """Cria repositórios ECR para cada microsserviço."""
    logger.info("=== Criando repositórios ECR ===")
    repos = {}

    for svc in SERVICES:
        repo_name = f"dijkfood/{svc['name']}"
        try:
            resp = ecr_client.create_repository(
                repositoryName=repo_name,
                imageScanningConfiguration={"scanOnPush": True},
            )
            repo_uri = resp["repository"]["repositoryUri"]
            logger.info(f"ECR repo criado: {repo_uri}")
        except ecr_client.exceptions.RepositoryAlreadyExistsException:
            resp = ecr_client.describe_repositories(repositoryNames=[repo_name])
            repo_uri = resp["repositories"][0]["repositoryUri"]
            logger.info(f"ECR repo já existe: {repo_uri}")

        repos[svc["name"]] = repo_uri

    return repos


def build_and_push_images(ecr_client, repos, account_id):
    """Build Docker images e push para ECR."""
    logger.info("=== Build & Push de imagens Docker ===")

    # Login no ECR
    token = ecr_client.get_authorization_token()
    auth_data = token["authorizationData"][0]
    registry = auth_data["proxyEndpoint"]

    subprocess.run(
        f"aws ecr get-login-password --region {REGION} | docker login --username AWS --password-stdin {registry}",
        shell=True, check=True
    )
    logger.info("Login no ECR realizado")

    services_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "services")

    for svc in SERVICES:
        svc_name = svc["name"]
        repo_uri = repos[svc_name]
        svc_dir = os.path.join(services_dir, svc_name)
        tag = f"{repo_uri}:latest"

        logger.info(f"Building {svc_name}...")
        subprocess.run(
            ["docker", "build", "-t", tag, svc_dir],
            check=True
        )

        logger.info(f"Pushing {svc_name}...")
        subprocess.run(
            ["docker", "push", tag],
            check=True
        )

        logger.info(f"Imagem {svc_name} pushed: {tag}")


def create_ecs_cluster(ecs_client):
    """Cria o cluster ECS."""
    logger.info("=== Criando ECS Cluster ===")

    try:
        resp = ecs_client.create_cluster(
            clusterName="dijkfood-cluster",
            capacityProviders=["FARGATE", "FARGATE_SPOT"],
            defaultCapacityProviderStrategy=[
                {"capacityProvider": "FARGATE", "weight": 1, "base": 1},
            ],
            settings=[
                {"name": "containerInsights", "value": "enabled"}
            ],
        )
        cluster_arn = resp["cluster"]["clusterArn"]
        logger.info(f"ECS Cluster criado: {cluster_arn}")
        return cluster_arn
    except Exception as e:
        logger.warning(f"Cluster pode já existir: {e}")
        resp = ecs_client.describe_clusters(clusters=["dijkfood-cluster"])
        return resp["clusters"][0]["clusterArn"]


def create_alb(elbv2_client, vpc_config):
    """Cria ALB + Target Groups + Listener Rules."""
    logger.info("=== Criando Application Load Balancer ===")

    # 1. Criar ALB
    alb = elbv2_client.create_load_balancer(
        Name="dijkfood-alb",
        Subnets=vpc_config["public_subnets"],
        SecurityGroups=[vpc_config["sg_alb_id"]],
        Scheme="internet-facing",
        Type="application",
        IpAddressType="ipv4",
        Tags=[{"Key": "Name", "Value": "dijkfood-alb"}],
    )
    alb_arn = alb["LoadBalancers"][0]["LoadBalancerArn"]
    alb_dns = alb["LoadBalancers"][0]["DNSName"]
    logger.info(f"ALB criado: {alb_dns}")

    # Aguardar ALB ativo
    waiter = elbv2_client.get_waiter("load_balancer_available")
    waiter.wait(LoadBalancerArns=[alb_arn])

    # 2. Criar Target Groups
    target_groups = {}
    for svc in SERVICES:
        tg = elbv2_client.create_target_group(
            Name=f"tg-{svc['name'][:28]}",  # max 32 chars
            Protocol="HTTP",
            Port=svc["port"],
            VpcId=vpc_config["vpc_id"],
            TargetType="ip",
            HealthCheckProtocol="HTTP",
            HealthCheckPath="/health",
            HealthCheckIntervalSeconds=30,
            HealthCheckTimeoutSeconds=10,
            HealthyThresholdCount=2,
            UnhealthyThresholdCount=5,
            Matcher={"HttpCode": "200"},
        )
        tg_arn = tg["TargetGroups"][0]["TargetGroupArn"]
        target_groups[svc["name"]] = tg_arn
        logger.info(f"Target Group criado: tg-{svc['name']}")

    # 3. Criar Listener (porta 80)
    # Default action: retornar 404 (fixed response)
    listener = elbv2_client.create_listener(
        LoadBalancerArn=alb_arn,
        Protocol="HTTP",
        Port=80,
        DefaultActions=[{
            "Type": "fixed-response",
            "FixedResponseConfig": {
                "MessageBody": '{"error": "Route not found"}',
                "StatusCode": "404",
                "ContentType": "application/json",
            }
        }],
    )
    listener_arn = listener["Listeners"][0]["ListenerArn"]
    logger.info("Listener HTTP:80 criado")

    # 4. Criar Listener Rules (path-based routing)
    for svc in SERVICES:
        conditions = [{
            "Field": "path-pattern",
            "Values": svc["path_patterns"]
        }]

        elbv2_client.create_rule(
            ListenerArn=listener_arn,
            Conditions=conditions,
            Priority=svc["priority"],
            Actions=[{
                "Type": "forward",
                "TargetGroupArn": target_groups[svc["name"]],
            }],
        )
        logger.info(f"Listener Rule criada: {svc['path_patterns']} → {svc['name']}")

    return {
        "alb_arn": alb_arn,
        "alb_dns": alb_dns,
        "listener_arn": listener_arn,
        "target_groups": target_groups,
    }


def create_task_definitions(ecs_client, repos, lab_role_arn, db_config, log_group_prefix="/ecs/dijkfood"):
    """Cria Task Definitions para cada microsserviço."""
    logger.info("=== Criando Task Definitions ===")

    task_defs = {}

    for svc in SERVICES:
        env_vars = [
            {"name": "AWS_REGION", "value": REGION},
            {"name": "KINESIS_STREAM", "value": "dijkfood-events"},
            {"name": "DYNAMODB_TABLE", "value": "courier_positions"},
        ]

        # DB config para serviços que usam RDS
        if svc["name"] in ["order-processor", "order-management", "conversational", "dashboard-analytics"]:
            env_vars.extend([
                {"name": "DB_HOST", "value": db_config["db_host"]},
                {"name": "DB_PORT", "value": str(db_config["db_port"])},
                {"name": "DB_NAME", "value": db_config["db_name"]},
                {"name": "DB_USER", "value": db_config["db_user"]},
                {"name": "DB_PASS", "value": db_config["db_pass"]},
            ])

        # ML Inference URL para order-processor e conversational
        if svc["name"] in ["order-processor", "conversational"]:
            env_vars.append(
                {"name": "ML_INFERENCE_URL", "value": "http://localhost:8004"}  # será resolvido via service discovery
            )

        # Bedrock config para conversational
        if svc["name"] == "conversational":
            env_vars.extend([
                {"name": "BEDROCK_PROFILE", "value": "bedrock"},
                {"name": "ATHENA_DATABASE", "value": "dijkfood_analytics"},
                {"name": "ATHENA_OUTPUT", "value": "s3://dijkfood-athena-results/"},
            ])
            # Injeta credenciais do Bedrock no ECS (lidas do ambiente de deploy)
            if os.environ.get("BEDROCK_AWS_ACCESS_KEY_ID"):
                env_vars.append({"name": "BEDROCK_AWS_ACCESS_KEY_ID", "value": os.environ.get("BEDROCK_AWS_ACCESS_KEY_ID")})
            if os.environ.get("BEDROCK_AWS_SECRET_ACCESS_KEY"):
                env_vars.append({"name": "BEDROCK_AWS_SECRET_ACCESS_KEY", "value": os.environ.get("BEDROCK_AWS_SECRET_ACCESS_KEY")})

        # Athena config para dashboard-analytics
        if svc["name"] == "dashboard-analytics":
            env_vars.extend([
                {"name": "ATHENA_DATABASE", "value": "dijkfood_analytics"},
                {"name": "ATHENA_OUTPUT", "value": "s3://dijkfood-athena-results/"},
                {"name": "DYNAMODB_TABLE", "value": "anomalies"},
            ])

        log_group = f"{log_group_prefix}/{svc['name']}"

        resp = ecs_client.register_task_definition(
            family=f"dijkfood-{svc['name']}",
            networkMode="awsvpc",
            requiresCompatibilities=["FARGATE"],
            cpu=svc["cpu"],
            memory=svc["memory"],
            executionRoleArn=lab_role_arn,
            taskRoleArn=lab_role_arn,
            containerDefinitions=[{
                "name": svc["name"],
                "image": f"{repos[svc['name']]}:latest",
                "portMappings": [{
                    "containerPort": svc["port"],
                    "hostPort": svc["port"],
                    "protocol": "tcp",
                }],
                "environment": env_vars,
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group": log_group,
                        "awslogs-region": REGION,
                        "awslogs-stream-prefix": "ecs",
                        "awslogs-create-group": "true",
                    }
                },
                "essential": True,
            }],
        )

        task_def_arn = resp["taskDefinition"]["taskDefinitionArn"]
        task_defs[svc["name"]] = task_def_arn
        logger.info(f"Task Definition criada: {svc['name']} — {task_def_arn}")

    return task_defs


def create_ecs_services(ecs_client, cluster_arn, task_defs, target_groups, vpc_config):
    """Cria ECS Services para cada microsserviço."""
    logger.info("=== Criando ECS Services ===")

    services = {}

    for svc in SERVICES:
        try:
            resp = ecs_client.create_service(
                cluster=cluster_arn,
                serviceName=f"dijkfood-{svc['name']}",
                taskDefinition=task_defs[svc["name"]],
                desiredCount=svc["min_tasks"],
                launchType="FARGATE",
                networkConfiguration={
                    "awsvpcConfiguration": {
                        "subnets": vpc_config["private_subnets"],
                        "securityGroups": [vpc_config["sg_ecs_id"]],
                        "assignPublicIp": "DISABLED",
                    }
                },
                loadBalancers=[{
                    "targetGroupArn": target_groups[svc["name"]],
                    "containerName": svc["name"],
                    "containerPort": svc["port"],
                }],
                deploymentConfiguration={
                    "maximumPercent": 200,
                    "minimumHealthyPercent": 50,
                    "deploymentCircuitBreaker": {
                        "enable": True,
                        "rollback": True,
                    },
                },
                healthCheckGracePeriodSeconds=60,
            )
            service_arn = resp["service"]["serviceArn"]
            services[svc["name"]] = service_arn
            logger.info(f"ECS Service criado: {svc['name']}")
        except Exception as e:
            logger.error(f"Erro ao criar service {svc['name']}: {e}")

    return services


def create_auto_scaling(aas_client, cluster_arn, services):
    """Configura Auto Scaling para cada serviço ECS."""
    logger.info("=== Configurando Auto Scaling ===")

    for svc in SERVICES:
        service_name = f"dijkfood-{svc['name']}"
        resource_id = f"service/dijkfood-cluster/{service_name}"

        try:
            # Registrar alvo escalável
            aas_client.register_scalable_target(
                ServiceNamespace="ecs",
                ResourceId=resource_id,
                ScalableDimension="ecs:service:DesiredCount",
                MinCapacity=svc["min_tasks"],
                MaxCapacity=svc["max_tasks"],
            )

            # Target Tracking em CPU
            aas_client.put_scaling_policy(
                PolicyName=f"{service_name}-cpu-scaling",
                ServiceNamespace="ecs",
                ResourceId=resource_id,
                ScalableDimension="ecs:service:DesiredCount",
                PolicyType="TargetTrackingScaling",
                TargetTrackingScalingPolicyConfiguration={
                    "TargetValue": float(svc["scaling_target"]),
                    "PredefinedMetricSpecification": {
                        "PredefinedMetricType": "ECSServiceAverageCPUUtilization",
                    },
                    "ScaleInCooldown": 300,
                    "ScaleOutCooldown": 60,
                },
            )
            logger.info(f"Auto Scaling configurado: {svc['name']} (target CPU {svc['scaling_target']}%)")

        except Exception as e:
            logger.error(f"Erro ao configurar Auto Scaling para {svc['name']}: {e}")


def wait_services_stable(ecs_client, cluster_arn):
    """Aguarda todos os serviços ECS atingirem estado estável."""
    logger.info("=== Aguardando estabilização dos serviços ECS ===")

    service_names = [f"dijkfood-{svc['name']}" for svc in SERVICES]

    try:
        waiter = ecs_client.get_waiter("services_stable")
        waiter.wait(
            cluster=cluster_arn,
            services=service_names,
            WaiterConfig={"Delay": 15, "MaxAttempts": 40}
        )
        logger.info("Todos os serviços ECS estáveis!")
    except Exception as e:
        logger.warning(f"Timeout ao aguardar estabilização: {e}")
        # Verificar status individual
        resp = ecs_client.describe_services(cluster=cluster_arn, services=service_names)
        for svc in resp["services"]:
            logger.info(f"  {svc['serviceName']}: {svc['status']} — running: {svc['runningCount']}/{svc['desiredCount']}")


def destroy_compute(ecs_client, elbv2_client, ecr_client, aas_client, alb_config, cluster_arn):
    """Destrói recursos de computação: Auto Scaling, ECS Services, ALB, ECR."""
    logger.info("=== Destruindo recursos de computação ===")

    # 1. Remover Auto Scaling
    for svc in SERVICES:
        resource_id = f"service/dijkfood-cluster/dijkfood-{svc['name']}"
        try:
            aas_client.deregister_scalable_target(
                ServiceNamespace="ecs",
                ResourceId=resource_id,
                ScalableDimension="ecs:service:DesiredCount",
            )
        except Exception as e:
            logger.warning(f"Erro ao remover Auto Scaling de {svc['name']}: {e}")

    # 2. Deletar ECS Services
    for svc in SERVICES:
        try:
            ecs_client.update_service(
                cluster=cluster_arn,
                service=f"dijkfood-{svc['name']}",
                desiredCount=0
            )
            ecs_client.delete_service(
                cluster=cluster_arn,
                service=f"dijkfood-{svc['name']}",
                force=True
            )
            logger.info(f"Service dijkfood-{svc['name']} deletado")
        except Exception as e:
            logger.warning(f"Erro ao deletar service {svc['name']}: {e}")

    # Aguardar services serem removidos
    time.sleep(30)

    # 3. Deletar ALB
    if alb_config:
        # Deletar listener
        try:
            elbv2_client.delete_listener(ListenerArn=alb_config["listener_arn"])
        except Exception:
            pass

        # Deletar target groups
        for tg_arn in alb_config.get("target_groups", {}).values():
            try:
                elbv2_client.delete_target_group(TargetGroupArn=tg_arn)
            except Exception:
                pass

        # Deletar ALB
        try:
            elbv2_client.delete_load_balancer(LoadBalancerArn=alb_config["alb_arn"])
            logger.info("ALB deletado")
            time.sleep(30)  # aguardar deletion
        except Exception as e:
            logger.warning(f"Erro ao deletar ALB: {e}")

    # 4. Deregistrar Task Definitions
    for svc in SERVICES:
        try:
            resp = ecs_client.list_task_definitions(familyPrefix=f"dijkfood-{svc['name']}")
            for td_arn in resp.get("taskDefinitionArns", []):
                ecs_client.deregister_task_definition(taskDefinition=td_arn)
        except Exception:
            pass

    # 5. Deletar cluster
    try:
        ecs_client.delete_cluster(cluster=cluster_arn)
        logger.info("ECS Cluster deletado")
    except Exception as e:
        logger.warning(f"Erro ao deletar cluster: {e}")

    # 6. Deletar ECR repos
    for svc in SERVICES:
        repo_name = f"dijkfood/{svc['name']}"
        try:
            ecr_client.delete_repository(repositoryName=repo_name, force=True)
            logger.info(f"ECR repo {repo_name} deletado")
        except Exception:
            pass
