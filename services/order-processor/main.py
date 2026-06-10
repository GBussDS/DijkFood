"""
DijkFood — Order Processor: FastAPI Application
Microsserviço responsável pela criação de pedidos com cálculo de rota via Dijkstra.
"""
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from models import HealthResponse
from graph_loader import load_graph, is_graph_loaded
from routes import router, init_db, close_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle: carrega o grafo de SP e inicializa DB no startup."""
    logger.info("=== Order Processor — Startup ===")

    # Carregar grafo em thread separada para não bloquear o event loop
    graph_thread = threading.Thread(target=load_graph, daemon=True)
    graph_thread.start()

    # Inicializar pool de conexões PostgreSQL
    await init_db()

    logger.info("Order Processor pronto para receber requisições")
    yield

    # Shutdown
    logger.info("=== Order Processor — Shutdown ===")
    await close_db()


app = FastAPI(
    title="DijkFood Order Processor",
    description="Criação de pedidos com cálculo de rota via Dijkstra sobre o grafo de SP",
    version="1.0.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rotas
app.include_router(router)


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        service="order-processor",
        graph_loaded=is_graph_loaded()
    )
