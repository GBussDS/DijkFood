"""
DijkFood — Order Processor: Route Handlers
"""
import json
import logging
from datetime import datetime
from uuid import uuid4

import aiohttp
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from models import CreateOrderRequest, CreateOrderResponse
from graph_loader import calculate_route

logger = logging.getLogger(__name__)
router = APIRouter()

# Configuração de serviços internos (via env vars)
import os

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "dijkfood")
DB_USER = os.environ.get("DB_USER", "dijkfood")
DB_PASS = os.environ.get("DB_PASS", "dijkfood")
KINESIS_STREAM = os.environ.get("KINESIS_STREAM", "dijkfood-events")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
ML_INFERENCE_URL = os.environ.get("ML_INFERENCE_URL", "http://ml-inference:8000")

# Pool de conexões PostgreSQL (inicializado no startup)
db_pool = None


async def init_db():
    """Inicializa o pool de conexões PostgreSQL."""
    global db_pool
    import asyncpg
    db_pool = await asyncpg.create_pool(
        host=DB_HOST, port=int(DB_PORT),
        database=DB_NAME, user=DB_USER, password=DB_PASS,
        min_size=5, max_size=20
    )
    logger.info("Pool de conexões PostgreSQL inicializado")


async def close_db():
    """Fecha o pool de conexões."""
    global db_pool
    if db_pool:
        await db_pool.close()


def get_kinesis_client():
    """Retorna o cliente Kinesis (boto3)."""
    import boto3
    return boto3.client("kinesis", region_name=AWS_REGION)


@router.get("/api/orders")
async def list_orders(
    status: Optional[str] = Query(None, description="Filtrar por status"),
    search: Optional[str] = Query(None, description="Buscar por Order ID (prefixo)"),
    limit: int = Query(50, ge=1, le=200),
):
    """Lista pedidos com filtros opcionais (para o dashboard frontend)."""
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not ready")

    async with db_pool.acquire() as conn:
        conditions = []
        params = []
        idx = 1

        if status:
            conditions.append(f"o.status = ${idx}")
            params.append(status)
            idx += 1

        if search:
            conditions.append(f"CAST(o.id AS TEXT) LIKE ${idx}")
            params.append(f"{search}%")
            idx += 1

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        rows = await conn.fetch(f"""
            SELECT o.id, o.status, c.name AS customer_name,
                   r.name AS restaurant_name,
                   COALESCE(cr.name, '—') AS courier_name,
                   o.estimated_time, o.created_at
            FROM orders o
            LEFT JOIN customers c ON o.customer_id = c.id
            LEFT JOIN restaurants r ON o.restaurant_id = r.id
            LEFT JOIN couriers cr ON o.courier_id = cr.id
            {where}
            ORDER BY o.created_at DESC
            LIMIT ${idx}
        """, *params)
        return [
            {
                "id": str(r["id"]),
                "status": r["status"],
                "customer_name": r["customer_name"],
                "restaurant_name": r["restaurant_name"],
                "courier_name": r["courier_name"],
                "estimated_time": float(r["estimated_time"]) if r["estimated_time"] else None,
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]


@router.post("/api/orders", response_model=CreateOrderResponse)
async def create_order(request: CreateOrderRequest):
    """
    Cria um novo pedido:
    1. Valida cliente e restaurante no RDS
    2. Encontra entregador disponível mais próximo
    3. Calcula rota via Dijkstra (osmnx)
    4. Obtém predição de tempo de entrega (ML Inference Engine)
    5. Persiste pedido no RDS
    6. Emite evento ORDER_CREATED para Kinesis
    """
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not ready")

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            # 1. Validar cliente
            customer = await conn.fetchrow(
                "SELECT id, name, latitude, longitude FROM customers WHERE id = $1",
                request.customer_id
            )
            if not customer:
                raise HTTPException(status_code=404, detail="Customer not found")

            # 2. Validar restaurante
            restaurant = await conn.fetchrow(
                "SELECT id, name, latitude, longitude FROM restaurants WHERE id = $1",
                request.restaurant_id
            )
            if not restaurant:
                raise HTTPException(status_code=404, detail="Restaurant not found")

            # 3. Encontrar entregador disponível mais próximo
            courier = await conn.fetchrow("""
                SELECT id, name, latitude, longitude,
                       ST_Distance(
                           ST_MakePoint(longitude, latitude)::geography,
                           ST_MakePoint($1, $2)::geography
                       ) as distance
                FROM couriers
                WHERE status = 'AVAILABLE'
                ORDER BY distance ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            """, restaurant["longitude"], restaurant["latitude"])

            if not courier:
                raise HTTPException(status_code=503, detail="No couriers available")

            # 4. Calcular rota (restaurante → cliente) via Dijkstra
            route_coords, travel_time = calculate_route(
                restaurant["latitude"], restaurant["longitude"],
                customer["latitude"], customer["longitude"]
            )

            # 5. Obter predição de tempo (ML Inference Engine)
            predicted_time = travel_time / 60.0  # fallback: converter segundos para minutos
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{ML_INFERENCE_URL}/api/predictions/delivery_time",
                        json={
                            "distance_meters": travel_time,
                            "hour": datetime.now().hour,
                            "day_of_week": datetime.now().weekday(),
                            "restaurant_lat": restaurant["latitude"],
                            "restaurant_lon": restaurant["longitude"],
                            "customer_lat": customer["latitude"],
                            "customer_lon": customer["longitude"],
                            "courier_distance_to_restaurant": float(courier["distance"]),
                            "active_orders_count": 0
                        },
                        timeout=aiohttp.ClientTimeout(total=5)
                    ) as resp:
                        if resp.status == 200:
                            ml_result = await resp.json()
                            predicted_time = ml_result.get("estimated_minutes", predicted_time)
            except Exception as e:
                logger.warning(f"ML Inference falhou, usando fallback: {e}")

            # 6. Criar pedido no banco
            order_id = uuid4()
            items_json = json.dumps([item.model_dump() for item in request.items])
            route_json = json.dumps(route_coords)

            await conn.execute("""
                INSERT INTO orders (id, customer_id, restaurant_id, courier_id,
                                    items, status, route, estimated_time, created_at)
                VALUES ($1, $2, $3, $4, $5, 'CONFIRMED', $6, $7, NOW())
            """, order_id, request.customer_id, request.restaurant_id,
                courier["id"], items_json, route_json, predicted_time)

            # Registrar evento CONFIRMED
            await conn.execute("""
                INSERT INTO order_events (id, order_id, status, timestamp)
                VALUES ($1, $2, 'CONFIRMED', NOW())
            """, uuid4(), order_id)

            # 7. Marcar entregador como ocupado
            await conn.execute(
                "UPDATE couriers SET status = 'BUSY' WHERE id = $1",
                courier["id"]
            )

    # 8. Emitir evento para Kinesis (fora da transação, assíncrono)
    try:
        kinesis = get_kinesis_client()
        kinesis.put_record(
            StreamName=KINESIS_STREAM,
            Data=json.dumps({
                "event_type": "ORDER_CREATED",
                "order_id": str(order_id),
                "customer_id": str(request.customer_id),
                "restaurant_id": str(request.restaurant_id),
                "courier_id": str(courier["id"]),
                "estimated_time": predicted_time,
                "latitude": restaurant["latitude"],
                "longitude": restaurant["longitude"],
                "timestamp": datetime.utcnow().isoformat()
            }),
            PartitionKey=str(order_id)
        )
    except Exception as e:
        logger.error(f"Falha ao emitir evento Kinesis: {e}")

    return CreateOrderResponse(
        order_id=order_id,
        courier_id=courier["id"],
        route=route_coords,
        estimated_delivery_time=predicted_time
    )
