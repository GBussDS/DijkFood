#!/usr/bin/env python3
"""
DijkFood A2 — Pipeline de Treinamento de Modelo ML
Extrai dados do Athena, treina modelo de GradientBoosting, salva em S3.

Modelos treinados:
  1. delivery_time_model.pkl — Predição de tempo de entrega
  2. demand_model.pkl — Predição de demanda por região/horário

Uso:
  python train_model.py --bucket dijkfood-models-ACCOUNT_ID
  python train_model.py --synthetic  # Treinar com dados sintéticos (sem Athena)
"""
import argparse
import json
import logging
import os
import time

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, r2_score

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("train_model")


def extract_data_from_athena(database="dijkfood_analytics", output_location="s3://dijkfood-athena-results/"):
    """Extrai dados de treinamento do Athena."""
    import boto3

    logger.info("Extraindo dados do Athena...")
    athena = boto3.client("athena", region_name="us-east-1")

    query = """
    SELECT
        e1.order_id,
        e1.timestamp as created_at,
        e2.timestamp as delivered_at,
        DATE_DIFF('minute', CAST(e1.timestamp AS TIMESTAMP), CAST(e2.timestamp AS TIMESTAMP)) as delivery_time_minutes,
        e1.estimated_time,
        EXTRACT(HOUR FROM CAST(e1.timestamp AS TIMESTAMP)) as hour,
        EXTRACT(DOW FROM CAST(e1.timestamp AS TIMESTAMP)) as day_of_week,
        e1.latitude as restaurant_lat,
        e1.longitude as restaurant_lon
    FROM events e1
    JOIN events e2 ON e1.order_id = e2.order_id
    WHERE e1.event_type = 'ORDER_CREATED'
      AND e2.event_type = 'STATUS_CHANGED'
      AND e2.new_status = 'DELIVERED'
    """

    response = athena.start_query_execution(
        QueryString=query,
        QueryExecutionContext={"Database": database},
        ResultConfiguration={"OutputLocation": output_location}
    )

    query_id = response["QueryExecutionId"]

    # Aguardar resultado
    for _ in range(60):
        result = athena.get_query_execution(QueryExecutionId=query_id)
        state = result["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            break
        elif state in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"Query falhou: {result['QueryExecution']['Status']}")
        time.sleep(2)

    # Buscar resultados
    results = athena.get_query_results(QueryExecutionId=query_id)
    rows = results["ResultSet"]["Rows"]

    if len(rows) <= 1:
        raise ValueError("Sem dados suficientes para treinamento.")

    headers = [col["VarCharValue"] for col in rows[0]["Data"]]
    data = []
    for row in rows[1:]:
        data.append({
            headers[i]: col.get("VarCharValue", None)
            for i, col in enumerate(row["Data"])
        })

    df = pd.DataFrame(data)
    logger.info(f"Extraídos {len(df)} registros do Athena")
    return df


def generate_synthetic_data(n_samples=5000):
    """Gera dados sintéticos para treinamento quando não há dados históricos."""
    logger.info(f"Gerando {n_samples} amostras sintéticas...")

    np.random.seed(42)

    data = {
        "distance_meters": np.random.uniform(500, 15000, n_samples),
        "hour": np.random.randint(0, 24, n_samples),
        "day_of_week": np.random.randint(0, 7, n_samples),
        "restaurant_lat": np.random.uniform(-23.65, -23.45, n_samples),
        "restaurant_lon": np.random.uniform(-46.75, -46.55, n_samples),
        "customer_lat": np.random.uniform(-23.65, -23.45, n_samples),
        "customer_lon": np.random.uniform(-46.75, -46.55, n_samples),
        "courier_distance": np.random.uniform(100, 5000, n_samples),
        "active_orders": np.random.randint(1, 50, n_samples),
    }

    df = pd.DataFrame(data)

    # Gerar target realístico
    base_time = df["distance_meters"] / 500  # 500m/min base
    hour_factor = np.where(
        (df["hour"] >= 11) & (df["hour"] <= 14), 1.3,
        np.where((df["hour"] >= 18) & (df["hour"] <= 21), 1.4, 1.0)
    )
    congestion = df["active_orders"] / 50 * 0.3 + 1.0
    noise = np.random.normal(0, 2, n_samples)

    df["delivery_time_minutes"] = np.maximum(
        base_time * hour_factor * congestion + noise, 5
    )

    return df


def train_delivery_time_model(df):
    """Treina modelo de predição de tempo de entrega."""
    logger.info("Treinando modelo de delivery_time...")

    feature_cols = [
        "distance_meters", "hour", "day_of_week",
        "restaurant_lat", "restaurant_lon",
        "customer_lat", "customer_lon",
        "courier_distance", "active_orders"
    ]

    # Garantir que todas as colunas existem
    for col in feature_cols:
        if col not in df.columns:
            df[col] = 0

    X = df[feature_cols].astype(float)
    y = df["delivery_time_minutes"].astype(float)

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    model = GradientBoostingRegressor(
        n_estimators=200,
        max_depth=6,
        learning_rate=0.1,
        min_samples_split=10,
        random_state=42
    )
    model.fit(X_train, y_train)

    # Métricas
    y_pred = model.predict(X_test)
    mae = mean_absolute_error(y_test, y_pred)
    r2 = r2_score(y_test, y_pred)

    logger.info(f"  MAE: {mae:.2f} minutos")
    logger.info(f"  R²:  {r2:.4f}")

    # Feature importance
    importances = dict(zip(feature_cols, model.feature_importances_))
    logger.info("  Feature Importance:")
    for feat, imp in sorted(importances.items(), key=lambda x: -x[1]):
        logger.info(f"    {feat}: {imp:.4f}")

    return model


def train_demand_model(df=None):
    """Treina modelo de predição de demanda."""
    logger.info("Treinando modelo de demand...")

    # Gerar dados sintéticos de demanda
    np.random.seed(42)
    n = 2000

    data = {
        "region_lat": np.random.uniform(-23.65, -23.45, n),
        "region_lon": np.random.uniform(-46.75, -46.55, n),
        "hour": np.random.randint(0, 24, n),
        "day_of_week": np.random.randint(0, 7, n),
    }
    df_demand = pd.DataFrame(data)

    # Target: pedidos/hora
    base = 10
    hour_factor = np.where(
        (df_demand["hour"] >= 11) & (df_demand["hour"] <= 14), 3.0,
        np.where(
            (df_demand["hour"] >= 18) & (df_demand["hour"] <= 21), 4.0,
            np.where(
                (df_demand["hour"] >= 7) & (df_demand["hour"] <= 9), 1.5,
                0.5
            )
        )
    )
    weekend = np.where(df_demand["day_of_week"] >= 5, 1.3, 1.0)
    df_demand["orders_per_hour"] = np.maximum(
        base * hour_factor * weekend + np.random.normal(0, 3, n), 0
    )

    X = df_demand[["region_lat", "region_lon", "hour", "day_of_week"]]
    y = df_demand["orders_per_hour"]

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    model = GradientBoostingRegressor(
        n_estimators=100, max_depth=4, random_state=42
    )
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    mae = mean_absolute_error(y_test, y_pred)
    r2 = r2_score(y_test, y_pred)

    logger.info(f"  MAE: {mae:.2f} pedidos/hora")
    logger.info(f"  R²:  {r2:.4f}")

    return model


def save_models(delivery_model, demand_model, bucket=None, local_dir="/tmp"):
    """Salva modelos localmente e opcionalmente no S3."""
    os.makedirs(local_dir, exist_ok=True)

    delivery_path = os.path.join(local_dir, "delivery_time_model.pkl")
    demand_path = os.path.join(local_dir, "demand_model.pkl")

    joblib.dump(delivery_model, delivery_path)
    joblib.dump(demand_model, demand_path)
    logger.info(f"Modelos salvos localmente: {local_dir}")

    if bucket:
        import boto3
        s3 = boto3.client("s3", region_name="us-east-1")

        s3.upload_file(delivery_path, bucket, "delivery_time_model.pkl")
        s3.upload_file(demand_path, bucket, "demand_model.pkl")
        logger.info(f"Modelos uploaded para s3://{bucket}/")


def main():
    parser = argparse.ArgumentParser(description="DijkFood ML Model Training")
    parser.add_argument("--bucket", help="S3 bucket para salvar modelos")
    parser.add_argument("--synthetic", action="store_true", help="Usar dados sintéticos (sem Athena)")
    parser.add_argument("--output", default="/tmp", help="Diretório local para modelos")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("  DijkFood — Pipeline de Treinamento ML")
    logger.info("=" * 60)

    # Extrair dados
    if args.synthetic:
        df = generate_synthetic_data()
    else:
        try:
            df = extract_data_from_athena()
        except Exception as e:
            logger.warning(f"Falha ao extrair do Athena: {e}. Usando dados sintéticos.")
            df = generate_synthetic_data()

    # Treinar modelos
    delivery_model = train_delivery_time_model(df)
    demand_model = train_demand_model()

    # Salvar
    save_models(delivery_model, demand_model, bucket=args.bucket, local_dir=args.output)

    logger.info("\n✅ Treinamento concluído!")


if __name__ == "__main__":
    main()
