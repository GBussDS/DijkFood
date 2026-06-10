"""
DijkFood — Position Tracker: FastAPI Application
Microsserviço de alta frequência para rastreamento de posição de entregadores.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from routes import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "position-tracker"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== Position Tracker — Startup ===")
    yield
    logger.info("=== Position Tracker — Shutdown ===")


app = FastAPI(
    title="DijkFood Position Tracker",
    description="Rastreamento de posição de entregadores em alta frequência (100ms)",
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

app.include_router(router)


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok", service="position-tracker")
