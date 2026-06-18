import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from models import HealthResponse
from routes import router, init_db, close_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== Order Management — Startup ===")
    await init_db()
    yield
    logger.info("=== Order Management — Shutdown ===")
    await close_db()


app = FastAPI(
    title="DijkFood Order Management",
    description="CRUD de clientes, restaurantes, entregadores e gerenciamento de status de pedidos",
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
    return HealthResponse(status="ok", service="order-management")
