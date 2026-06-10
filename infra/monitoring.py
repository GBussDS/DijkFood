"""
DijkFood — Infrastructure: CloudWatch Monitoring (Log Groups, Alarms, Dashboard)
"""
import json
import logging

import boto3

logger = logging.getLogger(__name__)

REGION = "us-east-1"
LOG_GROUPS = [
    "/ecs/dijkfood/order-processor",
    "/ecs/dijkfood/order-management",
    "/ecs/dijkfood/position-tracker",
    "/ecs/dijkfood/conversational",
    "/ecs/dijkfood/ml-inference",
]


def create_log_groups(logs_client):
    """Cria CloudWatch Log Groups para cada microsserviço."""
    logger.info("=== Criando CloudWatch Log Groups ===")

    for lg in LOG_GROUPS:
        try:
            logs_client.create_log_group(logGroupName=lg)
            logs_client.put_retention_policy(logGroupName=lg, retentionInDays=14)
            logger.info(f"Log Group criado: {lg}")
        except logs_client.exceptions.ResourceAlreadyExistsException:
            logger.info(f"Log Group já existe: {lg}")


def create_alarms(cw_client, alb_arn):
    """Cria alarmes do CloudWatch."""
    logger.info("=== Criando CloudWatch Alarms ===")

    # Extrair nome do ALB para a dimensão
    # ARN format: arn:aws:elasticloadbalancing:region:account:loadbalancer/app/name/id
    alb_suffix = "/".join(alb_arn.split(":")[-1].split("/")[1:])

    alarms = [
        {
            "AlarmName": "dijkfood-alb-5xx-high",
            "MetricName": "HTTPCode_ELB_5XX_Count",
            "Namespace": "AWS/ApplicationELB",
            "Statistic": "Sum",
            "Period": 300,
            "EvaluationPeriods": 1,
            "Threshold": 10,
            "ComparisonOperator": "GreaterThanThreshold",
            "Dimensions": [{"Name": "LoadBalancer", "Value": alb_suffix}],
            "AlarmDescription": "ALB retornando mais de 10 erros 5xx em 5 minutos",
        },
        {
            "AlarmName": "dijkfood-alb-latency-p95",
            "MetricName": "TargetResponseTime",
            "Namespace": "AWS/ApplicationELB",
            "ExtendedStatistic": "p95",
            "Period": 300,
            "EvaluationPeriods": 2,
            "Threshold": 0.5,  # 500ms
            "ComparisonOperator": "GreaterThanThreshold",
            "Dimensions": [{"Name": "LoadBalancer", "Value": alb_suffix}],
            "AlarmDescription": "Latência P95 do ALB acima de 500ms",
        },
    ]

    for alarm in alarms:
        try:
            cw_client.put_metric_alarm(**alarm)
            logger.info(f"Alarm criado: {alarm['AlarmName']}")
        except Exception as e:
            logger.error(f"Erro ao criar alarm {alarm['AlarmName']}: {e}")


def create_dashboard(cw_client, alb_arn):
    """Cria dashboard CloudWatch com métricas de todos os serviços."""
    logger.info("=== Criando CloudWatch Dashboard ===")

    alb_suffix = "/".join(alb_arn.split(":")[-1].split("/")[1:])

    dashboard_body = {
        "widgets": [
            {
                "type": "metric",
                "x": 0, "y": 0, "width": 12, "height": 6,
                "properties": {
                    "title": "ALB Request Count",
                    "metrics": [
                        ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", alb_suffix, {"stat": "Sum"}]
                    ],
                    "period": 60,
                    "region": REGION,
                }
            },
            {
                "type": "metric",
                "x": 12, "y": 0, "width": 12, "height": 6,
                "properties": {
                    "title": "ALB Response Time (P50/P95/P99)",
                    "metrics": [
                        ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", alb_suffix, {"stat": "p50"}],
                        ["...", {"stat": "p95"}],
                        ["...", {"stat": "p99"}],
                    ],
                    "period": 60,
                    "region": REGION,
                }
            },
            {
                "type": "metric",
                "x": 0, "y": 6, "width": 12, "height": 6,
                "properties": {
                    "title": "ALB HTTP Error Codes",
                    "metrics": [
                        ["AWS/ApplicationELB", "HTTPCode_ELB_4XX_Count", "LoadBalancer", alb_suffix, {"stat": "Sum"}],
                        ["...", "HTTPCode_ELB_5XX_Count", ".", ".", {"stat": "Sum"}],
                    ],
                    "period": 60,
                    "region": REGION,
                }
            },
            {
                "type": "metric",
                "x": 12, "y": 6, "width": 12, "height": 6,
                "properties": {
                    "title": "ECS CPU Utilization (All Services)",
                    "metrics": [
                        ["AWS/ECS", "CPUUtilization", "ClusterName", "dijkfood-cluster",
                         "ServiceName", f"dijkfood-{svc['name']}", {"stat": "Average"}]
                        for svc in [
                            {"name": "order-processor"},
                            {"name": "order-management"},
                            {"name": "position-tracker"},
                            {"name": "conversational"},
                            {"name": "ml-inference"},
                        ]
                    ],
                    "period": 60,
                    "region": REGION,
                }
            },
            {
                "type": "metric",
                "x": 0, "y": 12, "width": 12, "height": 6,
                "properties": {
                    "title": "ECS Memory Utilization (All Services)",
                    "metrics": [
                        ["AWS/ECS", "MemoryUtilization", "ClusterName", "dijkfood-cluster",
                         "ServiceName", f"dijkfood-{svc['name']}", {"stat": "Average"}]
                        for svc in [
                            {"name": "order-processor"},
                            {"name": "order-management"},
                            {"name": "position-tracker"},
                            {"name": "conversational"},
                            {"name": "ml-inference"},
                        ]
                    ],
                    "period": 60,
                    "region": REGION,
                }
            },
            {
                "type": "metric",
                "x": 12, "y": 12, "width": 12, "height": 6,
                "properties": {
                    "title": "Kinesis — IncomingRecords",
                    "metrics": [
                        ["AWS/Kinesis", "IncomingRecords", "StreamName", "dijkfood-events", {"stat": "Sum"}],
                        ["...", "IncomingBytes", ".", ".", {"stat": "Sum"}],
                    ],
                    "period": 60,
                    "region": REGION,
                }
            },
        ]
    }

    try:
        cw_client.put_dashboard(
            DashboardName="DijkFood-Operational",
            DashboardBody=json.dumps(dashboard_body)
        )
        logger.info("Dashboard 'DijkFood-Operational' criado")
    except Exception as e:
        logger.error(f"Erro ao criar dashboard: {e}")


def destroy_monitoring(cw_client, logs_client):
    """Destrói recursos de monitoramento."""
    logger.info("=== Destruindo recursos de monitoramento ===")

    # Deletar alarmes
    for alarm_name in ["dijkfood-alb-5xx-high", "dijkfood-alb-latency-p95"]:
        try:
            cw_client.delete_alarms(AlarmNames=[alarm_name])
            logger.info(f"Alarm '{alarm_name}' deletado")
        except Exception as e:
            logger.warning(f"Erro ao deletar alarm: {e}")

    # Deletar dashboard
    try:
        cw_client.delete_dashboards(DashboardNames=["DijkFood-Operational"])
        logger.info("Dashboard deletado")
    except Exception as e:
        logger.warning(f"Erro ao deletar dashboard: {e}")

    # Deletar log groups
    for lg in LOG_GROUPS:
        try:
            logs_client.delete_log_group(logGroupName=lg)
            logger.info(f"Log Group '{lg}' deletado")
        except Exception as e:
            logger.warning(f"Erro ao deletar log group '{lg}': {e}")
