import logging
import os

import numpy as np

logger = logging.getLogger(__name__)

delivery_model = None
demand_model = None
MODELS_LOADED = False


def load_models():

    global delivery_model, demand_model, MODELS_LOADED

    bucket = os.environ.get("MODELS_BUCKET", "dijkfood-models")
    aws_region = os.environ.get("AWS_REGION", "us-east-1")

    try:
        import boto3
        import joblib

        s3 = boto3.client("s3", region_name=aws_region)

        s3.download_file(bucket, "delivery_time_model.pkl", "/tmp/delivery_time_model.pkl")
        delivery_model = joblib.load("/tmp/delivery_time_model.pkl")
        logger.info("Modelo delivery_time carregado de S3")

        try:
            s3.download_file(bucket, "demand_model.pkl", "/tmp/demand_model.pkl")
            demand_model = joblib.load("/tmp/demand_model.pkl")
            logger.info("Modelo demand carregado de S3")
        except Exception:
            logger.warning("Modelo demand não encontrado em S3, usando fallback")
            demand_model = None

    except Exception as e:
        logger.warning(f"Não foi possível carregar modelos de S3: {e}. Usando modelos fallback.")
        delivery_model = None
        demand_model = None

    if delivery_model is None:
        delivery_model = create_fallback_delivery_model()

    if demand_model is None:
        demand_model = create_fallback_demand_model()

    MODELS_LOADED = True
    logger.info("Modelos carregados e prontos para inferência")


def create_fallback_delivery_model():

    from sklearn.ensemble import GradientBoostingRegressor

    logger.info("Criando modelo fallback de delivery_time com dados sintéticos...")

    np.random.seed(42)
    n_samples = 1000

    distance = np.random.uniform(500, 15000, n_samples)  # metros
    hour = np.random.randint(0, 24, n_samples)
    day_of_week = np.random.randint(0, 7, n_samples)
    rest_lat = np.random.uniform(-23.65, -23.45, n_samples)
    rest_lon = np.random.uniform(-46.75, -46.55, n_samples)
    cust_lat = np.random.uniform(-23.65, -23.45, n_samples)
    cust_lon = np.random.uniform(-46.75, -46.55, n_samples)
    courier_dist = np.random.uniform(100, 5000, n_samples)
    active_orders = np.random.randint(1, 50, n_samples)

    X = np.column_stack([distance, hour, day_of_week, rest_lat, rest_lon,
                         cust_lat, cust_lon, courier_dist, active_orders])

    base_time = distance / 500  # ~500m/min para moto/bike
    hour_factor = np.where((hour >= 11) & (hour <= 14), 1.3,
                  np.where((hour >= 18) & (hour <= 21), 1.4,
                  1.0))
    congestion = active_orders / 50 * 0.2 + 1.0
    y = base_time * hour_factor * congestion + np.random.normal(0, 2, n_samples)
    y = np.maximum(y, 5)

    model = GradientBoostingRegressor(n_estimators=100, max_depth=4, random_state=42)
    model.fit(X, y)

    logger.info("Modelo fallback de delivery_time criado")
    return model


def create_fallback_demand_model():

    from sklearn.ensemble import GradientBoostingRegressor

    logger.info("Criando modelo fallback de demand com dados sintéticos...")

    np.random.seed(42)
    n_samples = 500

    lat = np.random.uniform(-23.65, -23.45, n_samples)
    lon = np.random.uniform(-46.75, -46.55, n_samples)
    hour = np.random.randint(0, 24, n_samples)
    day_of_week = np.random.randint(0, 7, n_samples)

    X = np.column_stack([lat, lon, hour, day_of_week])

    base_demand = 10
    hour_factor = np.where((hour >= 11) & (hour <= 14), 3.0,
                  np.where((hour >= 18) & (hour <= 21), 4.0,
                  np.where((hour >= 7) & (hour <= 9), 1.5,
                  0.5)))
    weekend_factor = np.where(day_of_week >= 5, 1.3, 1.0)
    y = base_demand * hour_factor * weekend_factor + np.random.normal(0, 3, n_samples)
    y = np.maximum(y, 0)

    model = GradientBoostingRegressor(n_estimators=100, max_depth=4, random_state=42)
    model.fit(X, y)

    logger.info("Modelo fallback de demand criado")
    return model


def predict_delivery_time(features: np.ndarray) -> float:

    if delivery_model is None:
        return 30.0
    prediction = delivery_model.predict(features)[0]
    return max(round(float(prediction), 1), 5.0)


def predict_demand(features: np.ndarray) -> int:

    if demand_model is None:
        return 10
    prediction = demand_model.predict(features)[0]
    return max(round(float(prediction)), 0)


def are_models_loaded() -> bool:
    return MODELS_LOADED
