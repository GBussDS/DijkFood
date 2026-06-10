"""
DijkFood — Agente Conversacional: FastAPI Application
Microsserviço de IA conversacional com LangChain + Amazon Bedrock.
"""
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

import boto3
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agent import create_agent
from tools import init_db_pool, close_db_pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

KINESIS_STREAM = os.environ.get("KINESIS_STREAM", "dijkfood-events")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Agent executor (inicializado no startup)
executor = None


class ChatRequest(BaseModel):
    message: str
    history: Optional[List[dict]] = None


class ChatResponse(BaseModel):
    response: str


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "conversational"
    agent_loaded: bool = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global executor
    logger.info("=== Agente Conversacional — Startup ===")

    # Inicializar pool de DB para as tools
    await init_db_pool()

    # Criar agente LangChain
    try:
        executor = create_agent()
        logger.info("Agente LangChain criado com sucesso")
    except Exception as e:
        logger.error(f"Falha ao criar agente: {e}")
        executor = None

    yield

    logger.info("=== Agente Conversacional — Shutdown ===")
    await close_db_pool()


app = FastAPI(
    title="DijkFood Conversational Agent",
    description="Agente conversacional com IA (LangChain + Amazon Bedrock Claude)",
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


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Endpoint para perguntas em linguagem natural."""
    if executor is None:
        return ChatResponse(
            response="Desculpe, o agente conversacional não está disponível no momento."
        )

    try:
        # Converter histórico para formato LangChain
        chat_history = []
        if request.history:
            from langchain_core.messages import HumanMessage, AIMessage
            for msg in request.history:
                if msg.get("role") == "user":
                    chat_history.append(HumanMessage(content=msg["content"]))
                elif msg.get("role") == "assistant":
                    chat_history.append(AIMessage(content=msg["content"]))

        # Invocar agente
        result = await executor.ainvoke({
            "input": request.message,
            "chat_history": chat_history
        })

        bot_response = result.get("output", "Não consegui processar sua pergunta.")

    except Exception as e:
        logger.error(f"Erro no agente: {e}")
        bot_response = f"Desculpe, ocorreu um erro ao processar sua pergunta: {str(e)}"

    # Emitir evento de conversa para Kinesis
    try:
        kinesis = boto3.client("kinesis", region_name=AWS_REGION)
        kinesis.put_record(
            StreamName=KINESIS_STREAM,
            Data=json.dumps({
                "event_type": "CONVERSATION",
                "user_message": request.message,
                "bot_response": bot_response,
                "timestamp": datetime.utcnow().isoformat()
            }),
            PartitionKey=f"chat-{uuid4()}"
        )
    except Exception as e:
        logger.error(f"Falha ao emitir evento Kinesis: {e}")

    return ChatResponse(response=bot_response)


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        service="conversational",
        agent_loaded=executor is not None
    )
