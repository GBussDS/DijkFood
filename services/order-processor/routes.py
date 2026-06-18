import asyncio
import json
import logging
from datetime import datetime
from uuid import uuid4

import aiohttp
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from models import CreateOrderRequest, CreateOrderResponse
from graph_loader import calculate_route

logger = logging.getLogger(__name__)
router = APIRouter()

import os

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "dijkfood")
DB_USER = os.environ.get("DB_USER", "dijkfood")
DB_PASS = os.environ.get("DB_PASS", "dijkfood")
KINESIS_STREAM = os.environ.get("KINESIS_STREAM", "dijkfood-events")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
ML_INFERENCE_URL = os.environ.get("ML_INFERENCE_URL", "http://ml-inference:8000")

db_pool = None


async def init_db():
    global db_pool
    import asyncpg
    db_pool = await asyncpg.create_pool(
        host=DB_HOST, port=int(DB_PORT),
        database=DB_NAME, user=DB_USER, password=DB_PASS,
        min_size=2, max_size=10,
    )
    logger.info("Pool de conexões PostgreSQL inicializado")


async def close_db():
    global db_pool
    if db_pool:
        await db_pool.close()


def get_kinesis_client():
    import boto3
    return boto3.client("kinesis", region_name=AWS_REGION)


VALID_TRANSITIONS = {
    "CONFIRMED":        "PREPARING",
    "PREPARING":        "READY_FOR_PICKUP",
    "READY_FOR_PICKUP": "PICKED_UP",
    "PICKED_UP":        "IN_TRANSIT",
    "IN_TRANSIT":       "DELIVERED",
}


@router.post("/api/orders/reset")
async def reset_orders():
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not ready")

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            orders_updated = await conn.fetchval("""
                WITH upd AS (
                    UPDATE orders
                    SET status = 'DELIVERED', updated_at = NOW()
                    WHERE status NOT IN ('DELIVERED')
                    RETURNING id
                )
                SELECT COUNT(*) FROM upd
            """)
            couriers_updated = await conn.fetchval("""
                WITH upd AS (
                    UPDATE couriers
                    SET status = 'AVAILABLE'
                    WHERE status = 'BUSY'
                    RETURNING id
                )
                SELECT COUNT(*) FROM upd
            """)

    return {
        "orders_delivered": int(orders_updated or 0),
        "couriers_released": int(couriers_updated or 0),
    }


@router.patch("/api/orders/{order_id}/status")
async def update_order_status(order_id: UUID, req: dict):
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not ready")

    new_status = req.get("status") if isinstance(req, dict) else None
    if not new_status:
        raise HTTPException(status_code=422, detail="Campo 'status' obrigatório")

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            order = await conn.fetchrow(
                "SELECT status, courier_id FROM orders WHERE id = $1 FOR UPDATE",
                order_id,
            )
            if not order:
                raise HTTPException(status_code=404, detail="Order not found")

            current_status = order["status"]
            expected_next = VALID_TRANSITIONS.get(current_status)
            if expected_next != new_status:
                raise HTTPException(
                    status_code=409,
                    detail=f"Transição inválida: {current_status} → {new_status}. Esperado: {expected_next}",
                )

            await conn.execute(
                "UPDATE orders SET status = $1, updated_at = NOW() WHERE id = $2",
                new_status, order_id,
            )
            await conn.execute(
                "INSERT INTO order_events (id, order_id, status, timestamp) VALUES ($1, $2, $3, NOW())",
                uuid4(), order_id, new_status,
            )
            if new_status == "DELIVERED" and order["courier_id"]:
                await conn.execute(
                    "UPDATE couriers SET status = 'AVAILABLE' WHERE id = $1",
                    order["courier_id"],
                )

    try:
        kinesis = get_kinesis_client()
        kinesis.put_record(
            StreamName=KINESIS_STREAM,
            Data=json.dumps({
                "event_type": "STATUS_CHANGED",
                "order_id": str(order_id),
                "old_status": current_status,
                "new_status": new_status,
                "timestamp": datetime.utcnow().isoformat(),
            }),
            PartitionKey=str(order_id),
        )
    except Exception as e:
        logger.error(f"Falha ao emitir evento Kinesis: {e}")

    return {"order_id": str(order_id), "old_status": current_status, "new_status": new_status}


@router.get("/api/orders")
async def list_orders(
    status: Optional[str] = Query(None, description="Filtrar por status"),
    search: Optional[str] = Query(None, description="Buscar por Order ID (prefixo)"),
    limit: int = Query(50, ge=1, le=200),
):
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

        if not conditions:
            conditions.append(f"o.created_at > NOW() - INTERVAL '1 hour'")

        where = f"WHERE {' AND '.join(conditions)}"
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


@router.post("/api/orders", response_model=CreateOrderResponse, status_code=201)
async def create_order(request: CreateOrderRequest):
    """
    Cria um novo pedido

    Estrutura de desempenho:
    1+2. Lê cliente e restaurante (sem transação, só leitura)
    3. Dijkstra em thread pool (CPU-heavy, não bloqueia o event loop)
    4. ML inference fora de qualquer transação
    5.  Seleciona entregador + insere pedido + marca BUSY
    6. Kinesis
    """
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not ready")

    # 1+2. Validar cliente e restaurante
    async with db_pool.acquire() as conn:
        customer = await conn.fetchrow(
            "SELECT id, name, latitude, longitude FROM customers WHERE id = $1",
            request.customer_id,
        )
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        restaurant = await conn.fetchrow(
            "SELECT id, name, latitude, longitude FROM restaurants WHERE id = $1",
            request.restaurant_id,
        )
        if not restaurant:
            raise HTTPException(status_code=404, detail="Restaurant not found")

    # 3. Dijkstra
    import math
    loop = asyncio.get_event_loop()
    try:
        route_coords, travel_time = await asyncio.wait_for(
            loop.run_in_executor(
                None, calculate_route,
                restaurant["latitude"], restaurant["longitude"],
                customer["latitude"], customer["longitude"],
            ),
            timeout=0.25,
        )
    except (asyncio.TimeoutError, Exception):
        lat1 = math.radians(restaurant["latitude"])
        lon1 = math.radians(restaurant["longitude"])
        lat2 = math.radians(customer["latitude"])
        lon2 = math.radians(customer["longitude"])
        a = (math.sin((lat2 - lat1) / 2) ** 2
             + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
        distance_m = 6_371_000 * 2 * math.asin(math.sqrt(a))
        travel_time = distance_m / 8.0
        route_coords = [
            [restaurant["latitude"], restaurant["longitude"]],
            [customer["latitude"], customer["longitude"]],
        ]

    predicted_time = travel_time / 60.0  # fallback: segundos -> minutos
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
                    "courier_distance_to_restaurant": 0.0,
                    "active_orders_count": 0,
                },
                timeout=aiohttp.ClientTimeout(total=0.2),
            ) as resp:
                if resp.status == 200:
                    ml_result = await resp.json()
                    predicted_time = ml_result.get("estimated_minutes", predicted_time)
    except Exception as e:
        logger.warning(f"ML Inference falhou, usando fallback: {e}")

    # 5. só operações de banco —> seleciona entregador + insere + marca BUSY
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            courier = await conn.fetchrow("""
                SELECT id, name, latitude, longitude
                FROM couriers
                WHERE status = 'AVAILABLE'
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            """)

            if not courier:
                raise HTTPException(status_code=503, detail="No couriers available")

            order_id = uuid4()
            items_json = json.dumps([item.model_dump() for item in request.items])
            route_json = json.dumps(route_coords)

            await conn.execute("""
                INSERT INTO orders (id, customer_id, restaurant_id, courier_id,
                                    items, status, route, estimated_time, created_at)
                VALUES ($1, $2, $3, $4, $5, 'CONFIRMED', $6, $7, NOW())
            """, order_id, request.customer_id, request.restaurant_id,
                courier["id"], items_json, route_json, predicted_time)

            await conn.execute("""
                INSERT INTO order_events (id, order_id, status, timestamp)
                VALUES ($1, $2, 'CONFIRMED', NOW())
            """, uuid4(), order_id)

            await conn.execute(
                "UPDATE couriers SET status = 'BUSY' WHERE id = $1",
                courier["id"],
            )

    # 6. Kinesis
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
                "timestamp": datetime.utcnow().isoformat(),
            }),
            PartitionKey=str(order_id),
        )
    except Exception as e:
        logger.error(f"Falha ao emitir evento Kinesis: {e}")

    return CreateOrderResponse(
        order_id=order_id,
        courier_id=courier["id"],
        route=route_coords,
        estimated_delivery_time=predicted_time,
    )
