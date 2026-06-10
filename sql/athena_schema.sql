-- DijkFood A2 — Athena Schema (Reference Only)
-- A tabela será criada automaticamente pelo AWS Glue Crawler.
-- Este arquivo serve como referência do schema esperado.

-- O Glue Crawler irá descobrir o schema dos arquivos Parquet
-- no S3 e criar/atualizar a tabela automaticamente.

-- Schema esperado após o Crawler executar:
-- Database: dijkfood_analytics
-- Table: events
-- Format: Parquet (convertido pelo Firehose via Glue)
-- Location: s3://dijkfood-data-lake/events/
-- Particionamento: year, month, day, hour

-- Colunas esperadas:
--   event_type    STRING
--   order_id      STRING
--   customer_id   STRING
--   restaurant_id STRING
--   courier_id    STRING
--   old_status    STRING
--   new_status    STRING
--   latitude      DOUBLE
--   longitude     DOUBLE
--   estimated_time DOUBLE
--   user_message  STRING
--   bot_response  STRING
--   timestamp     STRING

-- Queries de referência para o Dashboard Analítico:

-- 1. Volume de pedidos no tempo (por hora)
-- SELECT DATE_TRUNC('hour', CAST(timestamp AS TIMESTAMP)) as hora,
--        COUNT(*) as total_pedidos
-- FROM events
-- WHERE event_type = 'ORDER_CREATED'
-- GROUP BY 1 ORDER BY 1;

-- 2. Tempo médio em cada estado do ciclo de vida
-- WITH status_times AS (
--     SELECT order_id, new_status, timestamp,
--            LAG(timestamp) OVER (PARTITION BY order_id ORDER BY timestamp) as prev_timestamp
--     FROM events
--     WHERE event_type = 'STATUS_CHANGED'
-- )
-- SELECT new_status,
--        AVG(DATE_DIFF('second', CAST(prev_timestamp AS TIMESTAMP), CAST(timestamp AS TIMESTAMP))) as avg_seconds
-- FROM status_times
-- WHERE prev_timestamp IS NOT NULL
-- GROUP BY new_status;

-- 3. Distribuição de pedidos por região (grid 0.01°)
-- SELECT ROUND(latitude, 2) as lat_grid,
--        ROUND(longitude, 2) as lon_grid,
--        COUNT(*) as total
-- FROM events
-- WHERE event_type = 'ORDER_CREATED'
-- GROUP BY 1, 2;

-- 4. Heatmap de demanda por horário e dia da semana
-- SELECT EXTRACT(DOW FROM CAST(timestamp AS TIMESTAMP)) as dia_semana,
--        EXTRACT(HOUR FROM CAST(timestamp AS TIMESTAMP)) as hora,
--        COUNT(*) as total
-- FROM events
-- WHERE event_type = 'ORDER_CREATED'
-- GROUP BY 1, 2;

-- 5. Top 10 restaurantes por volume
-- SELECT restaurant_id, COUNT(*) as total
-- FROM events
-- WHERE event_type = 'ORDER_CREATED'
-- GROUP BY 1
-- ORDER BY 2 DESC
-- LIMIT 10;

-- 6. Histograma do tempo total de entrega
-- WITH delivery_times AS (
--     SELECT e1.order_id,
--            DATE_DIFF('minute', CAST(e1.timestamp AS TIMESTAMP), CAST(e2.timestamp AS TIMESTAMP)) as total_minutes
--     FROM events e1
--     JOIN events e2 ON e1.order_id = e2.order_id
--     WHERE e1.event_type = 'ORDER_CREATED'
--       AND e2.event_type = 'STATUS_CHANGED' AND e2.new_status = 'DELIVERED'
-- )
-- SELECT FLOOR(total_minutes / 5) * 5 as bucket_min,
--        COUNT(*) as count
-- FROM delivery_times
-- GROUP BY 1
-- ORDER BY 1;
