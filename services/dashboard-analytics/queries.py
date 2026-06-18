import json
import logging
import time
from typing import Any, Dict, List, Optional

import boto3

logger = logging.getLogger(__name__)


# ============================================================
# ATHENA QUERIES
# ============================================================

ATHENA_ORDERS_PER_HOUR = """
SELECT EXTRACT(HOUR FROM CAST(timestamp AS TIMESTAMP)) AS hora,
       COUNT(*) AS total
FROM events
WHERE event_type = 'ORDER_CREATED'
GROUP BY 1
ORDER BY 1
"""

ATHENA_STATUS_TIMES = """
WITH status_times AS (
    SELECT order_id, new_status, timestamp,
           LAG(timestamp) OVER (PARTITION BY order_id ORDER BY timestamp) AS prev_timestamp
    FROM events
    WHERE event_type = 'STATUS_CHANGED'
)
SELECT new_status AS status,
       AVG(DATE_DIFF('second',
           CAST(prev_timestamp AS TIMESTAMP),
           CAST(timestamp AS TIMESTAMP)
       )) / 60.0 AS avg_minutes
FROM status_times
WHERE prev_timestamp IS NOT NULL
GROUP BY new_status
"""

ATHENA_TOP_RESTAURANTS = """
SELECT restaurant_id, COUNT(*) AS total
FROM events
WHERE event_type = 'ORDER_CREATED'
  AND restaurant_id IS NOT NULL
GROUP BY 1
ORDER BY 2 DESC
LIMIT 10
"""

ATHENA_DELIVERY_HISTOGRAM = """
WITH delivery_times AS (
    SELECT e1.order_id,
           DATE_DIFF('minute',
               CAST(e1.timestamp AS TIMESTAMP),
               CAST(e2.timestamp AS TIMESTAMP)
           ) AS total_minutes
    FROM events e1
    JOIN events e2 ON e1.order_id = e2.order_id
    WHERE e1.event_type = 'ORDER_CREATED'
      AND e2.event_type = 'STATUS_CHANGED'
      AND e2.new_status = 'DELIVERED'
)
SELECT FLOOR(total_minutes / 5) * 5 AS bucket_min,
       COUNT(*) AS count
FROM delivery_times
WHERE total_minutes > 0
GROUP BY 1
ORDER BY 1
"""

ATHENA_DEMAND_HEATMAP = """
SELECT EXTRACT(DOW FROM CAST(timestamp AS TIMESTAMP)) AS dia_semana,
       EXTRACT(HOUR FROM CAST(timestamp AS TIMESTAMP)) AS hora,
       COUNT(*) AS total
FROM events
WHERE event_type = 'ORDER_CREATED'
GROUP BY 1, 2
ORDER BY 1, 2
"""

ATHENA_REGION_DISTRIBUTION = """
SELECT region, COUNT(*) AS total
FROM events
WHERE event_type = 'ORDER_CREATED'
  AND region IS NOT NULL
GROUP BY 1
ORDER BY 2 DESC
"""


# ============================================================
# POSTGRES FALLBACK QUERIES (dados em tempo real via RDS)
# ============================================================

PG_SUMMARY = """
SELECT
    (SELECT COUNT(*) FROM orders WHERE created_at >= CURRENT_DATE) AS orders_today,
    (SELECT COUNT(*) FROM orders WHERE status NOT IN ('DELIVERED')) AS active_orders,
    (SELECT COUNT(*) FROM couriers WHERE status = 'AVAILABLE') AS available_couriers,
    (SELECT COALESCE(AVG(estimated_time), 0) FROM orders
     WHERE status = 'DELIVERED' AND created_at >= CURRENT_DATE) AS avg_delivery_time
"""

PG_ORDERS_PER_HOUR = """
SELECT EXTRACT(HOUR FROM created_at)::int AS hora,
       COUNT(*) AS total
FROM orders
WHERE created_at >= CURRENT_DATE
GROUP BY 1
ORDER BY 1
"""

PG_STATUS_TIMES = """
WITH status_transitions AS (
    SELECT oe.order_id, oe.status,
           oe.timestamp,
           LAG(oe.timestamp) OVER (PARTITION BY oe.order_id ORDER BY oe.timestamp) AS prev_ts
    FROM order_events oe
)
SELECT status,
       COALESCE(AVG(EXTRACT(EPOCH FROM (timestamp - prev_ts)) / 60.0), 0) AS avg_minutes
FROM status_transitions
WHERE prev_ts IS NOT NULL
GROUP BY status
"""

PG_TOP_RESTAURANTS = """
SELECT r.name AS restaurant_id, COUNT(o.id) AS total
FROM orders o
JOIN restaurants r ON o.restaurant_id = r.id
GROUP BY r.name
ORDER BY total DESC
LIMIT 10
"""

PG_DELIVERY_HISTOGRAM = """
SELECT
    FLOOR(estimated_time / 5) * 5 AS bucket_min,
    COUNT(*) AS count
FROM orders
WHERE status = 'DELIVERED'
  AND estimated_time IS NOT NULL
  AND estimated_time > 0
GROUP BY 1
ORDER BY 1
"""

PG_DEMAND_HEATMAP = """
SELECT EXTRACT(DOW FROM created_at)::int AS dia_semana,
       EXTRACT(HOUR FROM created_at)::int AS hora,
       COUNT(*) AS total
FROM orders
GROUP BY 1, 2
ORDER BY 1, 2
"""

PG_REGION_DISTRIBUTION = """
SELECT
    CASE
        WHEN r.latitude > -23.53 THEN 'Zona Norte'
        WHEN r.latitude < -23.57 THEN 'Zona Sul'
        WHEN r.longitude < -46.65 THEN 'Zona Oeste'
        WHEN r.longitude > -46.61 THEN 'Zona Leste'
        ELSE 'Centro'
    END AS region,
    COUNT(*) AS total
FROM orders o
JOIN restaurants r ON o.restaurant_id = r.id
GROUP BY 1
ORDER BY 2 DESC
"""


# ============================================================
# QUERY RUNNERS
# ============================================================

def run_athena_query(
    sql: str,
    region: str,
    database: str,
    output_location: str,
) -> Optional[List[Dict[str, Any]]]:
    try:
        client = boto3.client("athena", region_name=region)

        response = client.start_query_execution(
            QueryString=sql,
            QueryExecutionContext={"Database": database},
            ResultConfiguration={"OutputLocation": output_location},
        )
        query_id = response["QueryExecutionId"]

        for _ in range(30):
            result = client.get_query_execution(QueryExecutionId=query_id)
            state = result["QueryExecution"]["Status"]["State"]

            if state == "SUCCEEDED":
                results = client.get_query_results(QueryExecutionId=query_id)
                rows = results["ResultSet"]["Rows"]

                if len(rows) <= 1:
                    return []

                headers = [col["VarCharValue"] for col in rows[0]["Data"]]
                data = []
                for row in rows[1:]:
                    data.append({
                        headers[i]: col.get("VarCharValue", None)
                        for i, col in enumerate(row["Data"])
                    })
                return data

            elif state in ("FAILED", "CANCELLED"):
                reason = result["QueryExecution"]["Status"].get(
                    "StateChangeReason", "Unknown"
                )
                logger.error(f"Athena query falhou: {reason}")
                return None

            time.sleep(1)

        logger.error("Athena query timeout (30s)")
        return None

    except Exception as e:
        logger.error(f"Erro ao executar query Athena: {e}")
        return None


async def run_pg_query(pool, sql: str) -> List[Dict[str, Any]]:

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql)
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Erro ao executar query PostgreSQL: {e}")
        return []


async def run_pg_fetchrow(pool, sql: str) -> Optional[Dict[str, Any]]:

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql)
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"Erro ao executar query PostgreSQL: {e}")
        return None
