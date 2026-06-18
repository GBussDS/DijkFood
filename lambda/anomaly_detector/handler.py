import base64
import json
import logging
import time
from datetime import datetime
from uuid import uuid4

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
anomaly_table = dynamodb.Table("anomalies")
historical_table = dynamodb.Table("historical_averages")


def lambda_handler(event, context):
    """processa batch de records do Kinesis"""

    anomalies = []
    order_count = 0

    for record in event["Records"]:
        try:
            payload = json.loads(base64.b64decode(record["kinesis"]["data"]))
            event_type = payload.get("event_type")

            if event_type == "STATUS_CHANGED" and payload.get("new_status") == "DELIVERED":
                anomaly = check_slow_delivery(payload)
                if anomaly:
                    anomalies.append(anomaly)

            elif event_type == "ORDER_CREATED":
                order_count += 1

            elif event_type == "STATUS_CHANGED":
                anomaly = check_stuck_status(payload)
                if anomaly:
                    anomalies.append(anomaly)

        except Exception as e:
            logger.error(f"Erro ao processar record: {e}")
            continue

    # verificar spike de pedidos no batch
    if order_count > 0:
        anomaly = check_order_spike(order_count)
        if anomaly:
            anomalies.append(anomaly)

    # persistir anomalias detectadas
    for anomaly in anomalies:
        try:
            anomaly_table.put_item(Item={
                "id": str(uuid4()),
                "ttl": int(time.time()) + 86400,  # expira em 24h
                **anomaly
            })
            logger.info(f"Anomalia detectada: {anomaly['type']} - {anomaly.get('details', '')}")
        except Exception as e:
            logger.error(f"Erro ao salvar anomalia: {e}")

    logger.info(f"Processados {len(event['Records'])} records, {len(anomalies)} anomalias detectadas")

    return {
        "statusCode": 200,
        "anomalies_detected": len(anomalies),
        "records_processed": len(event["Records"])
    }


def check_slow_delivery(payload):
    """Verifica se o tempo de entrega é anômalo"""

    try:
        order_id = payload.get("order_id", "unknown")
        timestamp = payload.get("timestamp", "")

        try:
            hist = historical_table.get_item(
                Key={"metric": "avg_delivery_time", "dimension": "global"}
            )
            avg_time = float(hist.get("Item", {}).get("value", 1800))  # default 30min em segundos
        except Exception:
            avg_time = 1800  # 30 minutos default

        estimated = payload.get("estimated_time")
        if estimated and float(estimated) > avg_time / 60 * 2:  # estimated em minutos, avg em segundos
            return {
                "type": "SLOW_DELIVERY",
                "order_id": order_id,
                "estimated_time_minutes": float(estimated),
                "avg_time_minutes": avg_time / 60,
                "details": f"Tempo estimado {estimated}min é >{avg_time/60*2:.0f}min (2x média)",
                "timestamp": timestamp
            }

    except Exception as e:
        logger.error(f"Erro em check_slow_delivery: {e}")

    return None


def check_stuck_status(payload):
    """Verifica se um pedido está demorando muito em uma transição"""

    try:
        # detectar se a transição demorou muito
        old_status = payload.get("old_status", "")
        new_status = payload.get("new_status", "")

        # tempos máximos esperados por transição (em segundos)
        max_times = {
            "CONFIRMED_PREPARING": 300,
            "PREPARING_READY_FOR_PICKUP": 1800,
            "READY_FOR_PICKUP_PICKED_UP": 600,
            "PICKED_UP_IN_TRANSIT": 120,
            "IN_TRANSIT_DELIVERED": 3600,
        }

        key = f"{old_status}_{new_status}"

    except Exception as e:
        logger.error(f"Erro em check_stuck_status: {e}")

    return None


def check_order_spike(order_count):
    """Verifica se há spike na taxa de pedidos"""

    try:
        # consultar média histórica
        try:
            hist = historical_table.get_item(
                Key={"metric": "avg_orders_per_batch", "dimension": "global"}
            )
            avg_count = float(hist.get("Item", {}).get("value", 10))
        except Exception:
            avg_count = 10

        if order_count > avg_count * 3:
            return {
                "type": "ORDER_SPIKE",
                "current_count": order_count,
                "avg_count": avg_count,
                "details": f"Batch com {order_count} pedidos (3x a média de {avg_count})",
                "timestamp": datetime.utcnow().isoformat()
            }

    except Exception as e:
        logger.error(f"Erro em check_order_spike: {e}")

    return None
