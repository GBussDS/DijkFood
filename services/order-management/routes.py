"""
DijkFood — Order Management: Route Handlers
CRUD de clientes, restaurantes, entregadores + gerenciamento de status de pedidos.
"""
import asyncio
import json
import logging
import os
from datetime import datetime
from uuid import UUID, uuid4

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from models import (
    CreateCustomerRequest, CustomerResponse,
    CreateRestaurantRequest, RestaurantResponse,
    CreateCourierRequest, CourierResponse,
    OrderResponse, UpdateStatusRequest, OrderEventResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Configuração
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "dijkfood")
DB_USER = os.environ.get("DB_USER", "dijkfood")
DB_PASS = os.environ.get("DB_PASS", "dijkfood")
KINESIS_STREAM = os.environ.get("KINESIS_STREAM", "dijkfood-events")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

db_pool = None

# Transições de status válidas
VALID_TRANSITIONS = {
    "CONFIRMED": "PREPARING",
    "PREPARING": "READY_FOR_PICKUP",
    "READY_FOR_PICKUP": "PICKED_UP",
    "PICKED_UP": "IN_TRANSIT",
    "IN_TRANSIT": "DELIVERED",
}


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


# ===================================================================
# CUSTOMERS
# ===================================================================
@router.post("/api/customers", response_model=CustomerResponse, status_code=201)
async def create_customer(req: CreateCustomerRequest):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO customers (name, email, phone, latitude, longitude)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, name, email, phone, latitude, longitude, created_at
        """, req.name, req.email, req.phone, req.latitude, req.longitude)
        return CustomerResponse(**dict(row))


@router.get("/api/customers/{customer_id}", response_model=CustomerResponse)
async def get_customer(customer_id: UUID):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, email, phone, latitude, longitude, created_at FROM customers WHERE id = $1",
            customer_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="Customer not found")
        return CustomerResponse(**dict(row))


@router.get("/api/customers/{customer_id}/orders")
async def get_customer_orders(customer_id: UUID):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, customer_id, restaurant_id, courier_id, items, status,
                   route, estimated_time, created_at, updated_at
            FROM orders WHERE customer_id = $1
            ORDER BY created_at DESC
        """, customer_id)
        return [dict(r) for r in rows]


# ===================================================================
# RESTAURANTS
# ===================================================================
@router.post("/api/restaurants", response_model=RestaurantResponse, status_code=201)
async def create_restaurant(req: CreateRestaurantRequest):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO restaurants (name, cuisine_type, latitude, longitude)
            VALUES ($1, $2, $3, $4)
            RETURNING id, name, cuisine_type, latitude, longitude, created_at
        """, req.name, req.cuisine_type, req.latitude, req.longitude)
        return RestaurantResponse(**dict(row))


@router.get("/api/restaurants/{restaurant_id}", response_model=RestaurantResponse)
async def get_restaurant(restaurant_id: UUID):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, cuisine_type, latitude, longitude, created_at FROM restaurants WHERE id = $1",
            restaurant_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="Restaurant not found")
        return RestaurantResponse(**dict(row))


# ===================================================================
# COURIERS
# ===================================================================
@router.post("/api/couriers", response_model=CourierResponse, status_code=201)
async def create_courier(req: CreateCourierRequest):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO couriers (name, vehicle_type, latitude, longitude)
            VALUES ($1, $2, $3, $4)
            RETURNING id, name, vehicle_type, latitude, longitude, status, created_at
        """, req.name, req.vehicle_type, req.latitude, req.longitude)
        return CourierResponse(**dict(row))


@router.get("/api/couriers/{courier_id}", response_model=CourierResponse)
async def get_courier(courier_id: UUID):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, vehicle_type, latitude, longitude, status, created_at FROM couriers WHERE id = $1",
            courier_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="Courier not found")
        return CourierResponse(**dict(row))


@router.get("/api/couriers")
async def list_couriers():
    """Lista todos os entregadores com status atual (para o dashboard frontend)."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, name, vehicle_type, latitude, longitude, status
            FROM couriers
            ORDER BY status ASC, name ASC
        """)
        return [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "vehicle_type": r["vehicle_type"],
                "status": r["status"],
            }
            for r in rows
        ]


@router.get("/api/restaurants")
async def list_restaurants():
    """Lista restaurantes cadastrados."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, name, cuisine_type, latitude, longitude
            FROM restaurants ORDER BY name LIMIT 50
        """)
        return [{"id": str(r["id"]), "name": r["name"], "cuisine_type": r["cuisine_type"]} for r in rows]


# ===================================================================
# ORDERS — List & Status Management
# ===================================================================
@router.get("/api/orders")
async def list_orders(
    status: Optional[str] = Query(None, description="Filtrar por status"),
    search: Optional[str] = Query(None, description="Buscar por Order ID (prefixo)"),
    limit: int = Query(50, ge=1, le=200),
):
    """Lista pedidos com filtros opcionais (para o dashboard frontend)."""
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

        # Sem filtros explícitos: limita a última hora para evitar full scan
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


@router.get("/api/orders/{order_id}")
async def get_order(order_id: UUID):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT id, customer_id, restaurant_id, courier_id, items, status,
                   route, estimated_time, created_at, updated_at
            FROM orders WHERE id = $1
        """, order_id)
        if not row:
            raise HTTPException(status_code=404, detail="Order not found")
        return dict(row)


@router.patch("/api/orders/{order_id}/status")
async def update_order_status(order_id: UUID, req: UpdateStatusRequest):
    """
    Atualiza o status do pedido com validação de transição de estados.
    Retorna HTTP 409 se a transição for inválida.
    """
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            order = await conn.fetchrow(
                "SELECT status, courier_id FROM orders WHERE id = $1 FOR UPDATE",
                order_id
            )
            if not order:
                raise HTTPException(status_code=404, detail="Order not found")

            current_status = order["status"]
            expected_next = VALID_TRANSITIONS.get(current_status)

            if expected_next != req.status:
                raise HTTPException(
                    status_code=409,
                    detail=f"Invalid transition: {current_status} → {req.status}. Expected: {expected_next}"
                )

            # Atualizar status
            await conn.execute("""
                UPDATE orders SET status = $1, updated_at = NOW() WHERE id = $2
            """, req.status, order_id)

            # Registrar evento
            await conn.execute("""
                INSERT INTO order_events (id, order_id, status, timestamp)
                VALUES ($1, $2, $3, NOW())
            """, uuid4(), order_id, req.status)

            # Se DELIVERED, liberar entregador
            if req.status == "DELIVERED" and order["courier_id"]:
                await conn.execute(
                    "UPDATE couriers SET status = 'AVAILABLE' WHERE id = $1",
                    order["courier_id"]
                )

    # Emitir evento para Kinesis
    try:
        kinesis = get_kinesis_client()
        kinesis.put_record(
            StreamName=KINESIS_STREAM,
            Data=json.dumps({
                "event_type": "STATUS_CHANGED",
                "order_id": str(order_id),
                "old_status": current_status,
                "new_status": req.status,
                "timestamp": datetime.utcnow().isoformat()
            }),
            PartitionKey=str(order_id)
        )
    except Exception as e:
        logger.error(f"Falha ao emitir evento Kinesis: {e}")

    return {"order_id": str(order_id), "old_status": current_status, "new_status": req.status}


@router.post("/api/orders/reset")
async def reset_environment():
    """Avança todos os pedidos ativos para DELIVERED e libera entregadores."""
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            result = await conn.fetchrow("""
                WITH delivered AS (
                    UPDATE orders
                    SET status = 'DELIVERED', updated_at = NOW()
                    WHERE status NOT IN ('DELIVERED', 'CANCELLED')
                    RETURNING courier_id
                ),
                released AS (
                    UPDATE couriers
                    SET status = 'AVAILABLE'
                    WHERE id IN (SELECT courier_id FROM delivered WHERE courier_id IS NOT NULL)
                    RETURNING id
                )
                SELECT
                    (SELECT COUNT(*) FROM delivered) AS orders_delivered,
                    (SELECT COUNT(*) FROM released)  AS couriers_released
            """)
    return {
        "orders_delivered": result["orders_delivered"],
        "couriers_released": result["couriers_released"],
    }


@router.get("/api/orders/{order_id}/events")
async def get_order_events(order_id: UUID):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, order_id, status, timestamp
            FROM order_events WHERE order_id = $1
            ORDER BY timestamp ASC
        """, order_id)
        return [dict(r) for r in rows]
