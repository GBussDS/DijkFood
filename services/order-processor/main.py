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
    logger.info("=== Order Processor — Startup ===")

    graph_thread = threading.Thread(target=load_graph, daemon=True)
    graph_thread.start()

    await init_db()

    logger.info("Order Processor pronto para receber requisições")
    yield

    logger.info("=== Order Processor — Shutdown ===")
    await close_db()


app = FastAPI(
    title="DijkFood Order Processor",
    description="Criação de pedidos com cálculo de rota via Dijkstra sobre o grafo de SP",
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
    return HealthResponse(
        status="ok",
        service="order-processor",
        graph_loaded=is_graph_loaded()
    )
