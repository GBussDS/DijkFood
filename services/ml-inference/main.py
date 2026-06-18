import logging
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from model_loader import (
    load_models, predict_delivery_time, predict_demand, are_models_loaded
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


class DeliveryTimePredictionRequest(BaseModel):
    distance_meters: float
    hour: int
    day_of_week: int
    restaurant_lat: float
    restaurant_lon: float
    customer_lat: float
    customer_lon: float
    courier_distance_to_restaurant: float
    active_orders_count: int = 0


class DeliveryTimePredictionResponse(BaseModel):
    estimated_minutes: float


class DemandPredictionRequest(BaseModel):
    region_lat: float
    region_lon: float
    hour: int
    day_of_week: int


class DemandPredictionResponse(BaseModel):
    predicted_orders_per_hour: int


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "ml-inference"
    models_loaded: bool = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== ML Inference Engine — Startup ===")
    load_models()
    yield
    logger.info("=== ML Inference Engine — Shutdown ===")


app = FastAPI(
    title="DijkFood ML Inference Engine",
    description="Predições de tempo de entrega e demanda",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/predictions/delivery_time", response_model=DeliveryTimePredictionResponse)
async def predict_delivery_time_endpoint(request: DeliveryTimePredictionRequest):

    features = np.array([[
        request.distance_meters,
        request.hour,
        request.day_of_week,
        request.restaurant_lat,
        request.restaurant_lon,
        request.customer_lat,
        request.customer_lon,
        request.courier_distance_to_restaurant,
        request.active_orders_count
    ]])
    prediction = predict_delivery_time(features)
    return DeliveryTimePredictionResponse(estimated_minutes=prediction)


@app.post("/api/predictions/demand", response_model=DemandPredictionResponse)
async def predict_demand_endpoint(request: DemandPredictionRequest):

    features = np.array([[
        request.region_lat,
        request.region_lon,
        request.hour,
        request.day_of_week
    ]])
    prediction = predict_demand(features)
    return DemandPredictionResponse(predicted_orders_per_hour=prediction)


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        service="ml-inference",
        models_loaded=are_models_loaded()
    )
