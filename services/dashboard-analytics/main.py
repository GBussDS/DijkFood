import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import asyncpg
import boto3
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from queries import (
    ATHENA_ORDERS_PER_HOUR,
    ATHENA_STATUS_TIMES,
    ATHENA_TOP_RESTAURANTS,
    ATHENA_DELIVERY_HISTOGRAM,
    ATHENA_DEMAND_HEATMAP,
    ATHENA_REGION_DISTRIBUTION,
    PG_SUMMARY,
    PG_ORDERS_PER_HOUR,
    PG_STATUS_TIMES,
    PG_TOP_RESTAURANTS,
    PG_DELIVERY_HISTOGRAM,
    PG_DEMAND_HEATMAP,
    PG_REGION_DISTRIBUTION,
    run_athena_query,
    run_pg_query,
    run_pg_fetchrow,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ============================================================
# CONFIG
# ============================================================
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "dijkfood")
DB_USER = os.environ.get("DB_USER", "dijkfood")
DB_PASS = os.environ.get("DB_PASS", "dijkfood")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
ATHENA_DATABASE = os.environ.get("ATHENA_DATABASE", "")
ATHENA_OUTPUT = os.environ.get("ATHENA_OUTPUT", "")
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "anomalies")

athena_available: Optional[bool] = None
db_pool = None


# ============================================================
# LIFESPAN
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool
    logger.info("=== Dashboard Analytics — Startup ===")

    try:
        db_pool = await asyncpg.create_pool(
            host=DB_HOST,
            port=int(DB_PORT),
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            min_size=2,
            max_size=10,
        )
        logger.info("Pool PostgreSQL inicializado")
    except Exception as e:
        logger.error(f"Falha ao conectar PostgreSQL: {e}")
        db_pool = None

    yield

    logger.info("=== Dashboard Analytics — Shutdown ===")
    if db_pool:
        await db_pool.close()


# ============================================================
# APP
# ============================================================
app = FastAPI(
    title="DijkFood Dashboard Analytics",
    description="Métricas operacionais e analíticas para o dashboard frontend",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# HELPERS
# ============================================================
async def _try_athena(sql: str) -> Optional[List[Dict[str, Any]]]:

    global athena_available
    if athena_available is False:
        return None
    if not ATHENA_DATABASE or not ATHENA_OUTPUT:
        athena_available = False
        return None
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, run_athena_query, sql, AWS_REGION, ATHENA_DATABASE, ATHENA_OUTPUT
    )
    if result is not None:
        athena_available = True
    else:
        athena_available = False
    return result


def _safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        return round(float(val), 2)
    except (ValueError, TypeError):
        return default


def _safe_int(val, default=0):
    if val is None:
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


# ============================================================
# MODELS
# ============================================================
class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "dashboard-analytics"
    db_connected: bool = False
    athena_available: Optional[bool] = None


# ============================================================
# ENDPOINTS
# ============================================================

@app.get("/health", response_model=HealthResponse)
async def health():

    return HealthResponse(
        status="ok",
        service="dashboard-analytics",
        db_connected=db_pool is not None,
        athena_available=athena_available,
    )


@app.get("/api/dashboard/summary")
async def dashboard_summary():

    if db_pool is None:
        return {"orders_today": 0, "active_orders": 0,
                "available_couriers": 0, "avg_delivery_time": 0}

    row = await run_pg_fetchrow(db_pool, PG_SUMMARY)
    if not row:
        return {"orders_today": 0, "active_orders": 0,
                "available_couriers": 0, "avg_delivery_time": 0}

    return {
        "orders_today": _safe_int(row.get("orders_today")),
        "active_orders": _safe_int(row.get("active_orders")),
        "available_couriers": _safe_int(row.get("available_couriers")),
        "avg_delivery_time": _safe_float(row.get("avg_delivery_time")),
    }


@app.get("/api/dashboard/orders-per-hour")
async def orders_per_hour():

    data = await _try_athena(ATHENA_ORDERS_PER_HOUR)
    if data:
        hour_map = {_safe_int(r["hora"]): _safe_int(r["total"]) for r in data}
        return {
            "labels": [f"{h}h" for h in range(24)],
            "data": [hour_map.get(h, 0) for h in range(24)],
            "source": "athena",
        }

    if db_pool:
        rows = await run_pg_query(db_pool, PG_ORDERS_PER_HOUR)
        hour_map = {_safe_int(r["hora"]): _safe_int(r["total"]) for r in rows}
        return {
            "labels": [f"{h}h" for h in range(24)],
            "data": [hour_map.get(h, 0) for h in range(24)],
            "source": "postgres",
        }

    return {"labels": [], "data": [], "source": "none"}


@app.get("/api/dashboard/status-times")
async def status_times():

    data = await _try_athena(ATHENA_STATUS_TIMES)
    if data:
        return {
            "labels": [r["status"] for r in data],
            "data": [_safe_float(r["avg_minutes"]) for r in data],
            "source": "athena",
        }

    if db_pool:
        rows = await run_pg_query(db_pool, PG_STATUS_TIMES)
        return {
            "labels": [r["status"] for r in rows],
            "data": [_safe_float(r["avg_minutes"]) for r in rows],
            "source": "postgres",
        }

    return {"labels": [], "data": [], "source": "none"}


@app.get("/api/dashboard/top-restaurants")
async def top_restaurants():

    data = await _try_athena(ATHENA_TOP_RESTAURANTS)
    if data:
        return {
            "labels": [r["restaurant_id"] for r in data],
            "data": [_safe_int(r["total"]) for r in data],
            "source": "athena",
        }

    if db_pool:
        rows = await run_pg_query(db_pool, PG_TOP_RESTAURANTS)
        return {
            "labels": [r["restaurant_id"] for r in rows],
            "data": [_safe_int(r["total"]) for r in rows],
            "source": "postgres",
        }

    return {"labels": [], "data": [], "source": "none"}


@app.get("/api/dashboard/delivery-histogram")
async def delivery_histogram():

    data = await _try_athena(ATHENA_DELIVERY_HISTOGRAM)
    if data:
        return {
            "labels": [f"{_safe_int(r['bucket_min'])}-{_safe_int(r['bucket_min'])+5}"
                       for r in data],
            "data": [_safe_int(r["count"]) for r in data],
            "source": "athena",
        }

    if db_pool:
        rows = await run_pg_query(db_pool, PG_DELIVERY_HISTOGRAM)
        return {
            "labels": [f"{_safe_int(r['bucket_min'])}-{_safe_int(r['bucket_min'])+5}"
                       for r in rows],
            "data": [_safe_int(r["count"]) for r in rows],
            "source": "postgres",
        }

    return {"labels": [], "data": [], "source": "none"}


@app.get("/api/dashboard/demand-heatmap")
async def demand_heatmap():

    data = await _try_athena(ATHENA_DEMAND_HEATMAP)
    if data:
        points = [
            {"x": _safe_int(r["hora"]), "y": _safe_int(r["dia_semana"]),
             "v": _safe_int(r["total"])}
            for r in data
        ]
        return {"data": points, "source": "athena"}

    if db_pool:
        rows = await run_pg_query(db_pool, PG_DEMAND_HEATMAP)
        points = [
            {"x": _safe_int(r["hora"]), "y": _safe_int(r["dia_semana"]),
             "v": _safe_int(r["total"])}
            for r in rows
        ]
        return {"data": points, "source": "postgres"}

    return {"data": [], "source": "none"}


@app.get("/api/dashboard/region-distribution")
async def region_distribution():

    data = await _try_athena(ATHENA_REGION_DISTRIBUTION)
    if data:
        return {
            "labels": [r["region"] for r in data],
            "data": [_safe_int(r["total"]) for r in data],
            "source": "athena",
        }

    if db_pool:
        rows = await run_pg_query(db_pool, PG_REGION_DISTRIBUTION)
        return {
            "labels": [r["region"] for r in rows],
            "data": [_safe_int(r["total"]) for r in rows],
            "source": "postgres",
        }

    return {"labels": [], "data": [], "source": "none"}


@app.get("/api/dashboard/anomalies")
async def anomalies():

    try:
        dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
        table = dynamodb.Table(DYNAMODB_TABLE)

        response = table.scan(
            FilterExpression="attribute_exists(#t) AND #t > :now",
            ExpressionAttributeNames={"#t": "ttl"},
            ExpressionAttributeValues={":now": int(time.time())},
        )

        items = response.get("Items", [])
        return {
            "anomalies": [
                {
                    "type": item.get("anomaly_type", "unknown"),
                    "description": item.get("description", ""),
                    "severity": item.get("severity", "medium"),
                    "detected_at": item.get("detected_at", ""),
                }
                for item in items
            ],
            "count": len(items),
        }

    except Exception as e:
        logger.warning(f"DynamoDB anomalies indisponível: {e}")
        return {"anomalies": [], "count": 0}
