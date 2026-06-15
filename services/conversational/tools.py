"""
DijkFood — Agente Conversacional: Tools
Ferramentas que o agente LangChain pode usar para buscar dados.
"""
import json
import logging
import os
import time
from decimal import Decimal
from datetime import datetime, date
from uuid import UUID

import asyncpg
import boto3

logger = logging.getLogger(__name__)

# Configurações
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "dijkfood")
DB_USER = os.environ.get("DB_USER", "dijkfood")
DB_PASS = os.environ.get("DB_PASS", "dijkfood")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
ATHENA_DATABASE = os.environ.get("ATHENA_DATABASE", "dijkfood_analytics")
ATHENA_OUTPUT = os.environ.get("ATHENA_OUTPUT", "s3://dijkfood-athena-results/")
ML_INFERENCE_URL = os.environ.get("ML_INFERENCE_URL", "http://ml-inference:8000")
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "courier_positions")

# Pool de conexões PostgreSQL e referência ao event loop principal
db_pool = None
_main_loop = None


async def init_db_pool():
    global db_pool, _main_loop
    import asyncio
    _main_loop = asyncio.get_running_loop()
    db_pool = await asyncpg.create_pool(
        host=DB_HOST, port=int(DB_PORT),
        database=DB_NAME, user=DB_USER, password=DB_PASS,
        min_size=2, max_size=10
    )


async def close_db_pool():
    global db_pool
    if db_pool:
        await db_pool.close()


def query_athena(sql: str) -> str:
    """
    Executa uma consulta SQL no Amazon Athena sobre o data lake de eventos.
    Usado para dados históricos e agregados: volume de pedidos, tempos médios,
    distribuições, rankings de restaurantes, heatmaps.
    """
    try:
        client = boto3.client("athena", region_name=AWS_REGION)

        response = client.start_query_execution(
            QueryString=sql,
            QueryExecutionContext={"Database": ATHENA_DATABASE},
            ResultConfiguration={"OutputLocation": ATHENA_OUTPUT}
        )

        query_execution_id = response["QueryExecutionId"]

        # Aguardar resultado (polling com timeout)
        for _ in range(30):  # max 30 tentativas (30s)
            result = client.get_query_execution(QueryExecutionId=query_execution_id)
            state = result["QueryExecution"]["Status"]["State"]

            if state == "SUCCEEDED":
                # Buscar resultados
                results = client.get_query_results(QueryExecutionId=query_execution_id)
                rows = results["ResultSet"]["Rows"]

                if len(rows) <= 1:
                    return "Nenhum resultado encontrado."

                # Formatar como tabela
                headers = [col["VarCharValue"] for col in rows[0]["Data"]]
                data_rows = []
                for row in rows[1:]:
                    data_rows.append({
                        headers[i]: col.get("VarCharValue", "NULL")
                        for i, col in enumerate(row["Data"])
                    })

                return json.dumps(data_rows, indent=2, ensure_ascii=False)

            elif state in ("FAILED", "CANCELLED"):
                reason = result["QueryExecution"]["Status"].get("StateChangeReason", "Unknown")
                return f"Query falhou: {reason}"

            time.sleep(1)

        return "Query timeout após 30 segundos."

    except Exception as e:
        logger.error(f"Erro no Athena: {e}")
        return f"Erro ao executar query no Athena: {str(e)}"


def _to_json_safe(v):
    """Converte valores asyncpg para tipos serializáveis em JSON sem perder semântica.

    str(None) → "None" (string) faz o modelo reportar "NULL" nos dados; aqui
    mantemos None → null no JSON, e convertemos apenas tipos que json.dumps
    não suporta nativamente (UUID, datetime, Decimal).
    """
    if v is None:
        return None
    if isinstance(v, (UUID, datetime, date)):
        return str(v)
    if isinstance(v, Decimal):
        return float(v)
    return v


async def query_orders_db(query_description: str) -> str:
    """
    Consulta dados operacionais em tempo real do banco PostgreSQL.
    Use para status de pedidos específicos, contagens atuais, dados de clientes/restaurantes.
    Parâmetro: descrição da consulta desejada em linguagem natural.
    """
    try:
        if db_pool is None:
            return "Database não inicializado"

        async with db_pool.acquire() as conn:
            # Interpretar tipos comuns de consulta
            query_lower = query_description.lower()

            if "contagem" in query_lower or "quantos pedidos" in query_lower or "count" in query_lower:
                if "hoje" in query_lower:
                    result = await conn.fetchrow(
                        "SELECT COUNT(*) as total FROM orders WHERE created_at >= CURRENT_DATE"
                    )
                elif "hora" in query_lower:
                    result = await conn.fetchrow(
                        "SELECT COUNT(*) as total FROM orders WHERE created_at >= NOW() - INTERVAL '1 hour'"
                    )
                else:
                    result = await conn.fetchrow("SELECT COUNT(*) as total FROM orders")
                return json.dumps({"total_orders": result["total"]})

            elif "status" in query_lower and ("pedido" in query_lower or "order" in query_lower):
                # Extrair possível UUID do texto
                import re
                uuid_match = re.search(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                    query_description
                )
                if uuid_match:
                    from uuid import UUID
                    order_id = UUID(uuid_match.group())
                    result = await conn.fetchrow(
                        "SELECT id, status, courier_id, estimated_time, created_at, updated_at FROM orders WHERE id = $1",
                        order_id
                    )
                    if result:
                        return json.dumps({k: _to_json_safe(v) for k, v in dict(result).items()})
                    return "Pedido não encontrado."
                else:
                    # Contagem por status
                    rows = await conn.fetch(
                        "SELECT status, COUNT(*) as total FROM orders GROUP BY status ORDER BY total DESC"
                    )
                    return json.dumps([dict(r) for r in rows], default=str)

            elif "restaurante" in query_lower or "restaurant" in query_lower:
                if "mais pedidos" in query_lower or "top" in query_lower:
                    rows = await conn.fetch("""
                        SELECT r.name, COUNT(o.id) as total
                        FROM orders o JOIN restaurants r ON o.restaurant_id = r.id
                        GROUP BY r.name ORDER BY total DESC LIMIT 10
                    """)
                    return json.dumps([dict(r) for r in rows], default=str)
                else:
                    rows = await conn.fetch(
                        "SELECT id, name, cuisine_type, latitude, longitude FROM restaurants LIMIT 20"
                    )
                    return json.dumps([{k: _to_json_safe(v) for k, v in dict(r).items()} for r in rows])

            elif "entregador" in query_lower or "courier" in query_lower:
                if "disponível" in query_lower or "available" in query_lower:
                    result = await conn.fetchrow(
                        "SELECT COUNT(*) as total FROM couriers WHERE status = 'AVAILABLE'"
                    )
                    return json.dumps({"available_couriers": result["total"]})
                else:
                    rows = await conn.fetch(
                        "SELECT id, name, vehicle_type, status FROM couriers LIMIT 20"
                    )
                    return json.dumps([{k: _to_json_safe(v) for k, v in dict(r).items()} for r in rows])

            elif "tempo médio" in query_lower or "average" in query_lower:
                result = await conn.fetchrow("""
                    SELECT AVG(estimated_time) as avg_estimated_time
                    FROM orders WHERE status = 'DELIVERED' AND created_at >= CURRENT_DATE
                """)
                avg = result["avg_estimated_time"]
                return json.dumps({"avg_delivery_time_minutes": round(float(avg), 1) if avg else 0})

            else:
                # Query genérica: resumo operacional
                result = await conn.fetchrow("""
                    SELECT
                        (SELECT COUNT(*) FROM orders WHERE created_at >= CURRENT_DATE) as orders_today,
                        (SELECT COUNT(*) FROM orders WHERE status NOT IN ('DELIVERED')) as active_orders,
                        (SELECT COUNT(*) FROM couriers WHERE status = 'AVAILABLE') as available_couriers,
                        (SELECT AVG(estimated_time) FROM orders WHERE status = 'DELIVERED' AND created_at >= CURRENT_DATE) as avg_delivery_time
                """)
                return json.dumps({k: _to_json_safe(v) for k, v in dict(result).items()})

    except Exception as e:
        logger.error(f"Erro ao consultar PostgreSQL: {e}")
        return f"Erro: {str(e)}"


def get_courier_position(courier_id: str) -> str:
    """
    Obtém a posição atual de um entregador do DynamoDB.
    Use para rastrear a localização em tempo real de um entregador específico.
    """
    try:
        dynamodb = boto3.client("dynamodb", region_name=AWS_REGION)
        response = dynamodb.get_item(
            TableName=DYNAMODB_TABLE,
            Key={"courier_id": {"S": courier_id}}
        )

        item = response.get("Item")
        if not item:
            return f"Posição não encontrada para o entregador {courier_id}"

        return json.dumps({
            "courier_id": item["courier_id"]["S"],
            "latitude": float(item["latitude"]["N"]),
            "longitude": float(item["longitude"]["N"]),
            "order_id": item["order_id"]["S"],
            "timestamp": item["timestamp"]["S"]
        })

    except Exception as e:
        logger.error(f"Erro ao consultar DynamoDB: {e}")
        return f"Erro: {str(e)}"


def get_prediction(prediction_type: str) -> str:
    """
    Obtém predições do ML Inference Engine.
    Tipos: 'delivery_time' (tempo de entrega), 'demand' (demanda por região/horário).
    """
    try:
        import urllib.request
        import urllib.error

        if "demand" in prediction_type.lower() or "demanda" in prediction_type.lower():
            url = f"{ML_INFERENCE_URL}/api/predictions/demand"
            from datetime import datetime
            data = json.dumps({
                "region_lat": -23.55,
                "region_lon": -46.63,
                "hour": datetime.now().hour,
                "day_of_week": datetime.now().weekday()
            }).encode()
        else:
            url = f"{ML_INFERENCE_URL}/api/predictions/delivery_time"
            data = json.dumps({
                "distance_meters": 5000,
                "hour": 12,
                "day_of_week": 2,
                "restaurant_lat": -23.55,
                "restaurant_lon": -46.63,
                "customer_lat": -23.56,
                "customer_lon": -46.64,
                "courier_distance_to_restaurant": 1000,
                "active_orders_count": 10
            }).encode()

        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.read().decode()

    except Exception as e:
        logger.error(f"Erro no ML Inference: {e}")
        return f"Serviço de predição indisponível: {str(e)}"


def detect_anomalies() -> str:
    """
    Verifica anomalias operacionais ativas.
    Detecta: tempos de entrega anormais, regiões sem entregadores,
    picos de pedidos, atrasos em transições de status.
    """
    try:
        dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
        table = dynamodb.Table("anomalies")

        response = table.scan(
            FilterExpression="attribute_exists(#t) AND #t > :now",
            ExpressionAttributeNames={"#t": "ttl"},
            ExpressionAttributeValues={":now": int(time.time())}
        )

        items = response.get("Items", [])
        if not items:
            return "Nenhuma anomalia operacional detectada no momento."

        return json.dumps(items, indent=2, ensure_ascii=False, default=str)

    except Exception as e:
        logger.error(f"Erro ao verificar anomalias: {e}")
        return f"Erro ao verificar anomalias: {str(e)}"
