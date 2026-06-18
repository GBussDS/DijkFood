"""
DijkFood — Agente Conversacional: FastAPI Application
Microsserviço de IA conversacional com LangChain + Amazon Bedrock.
"""
import json
import logging
import os
import re
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


def _normalize_output(output) -> str:
    """
    Converte a saída do agente em string.

    Com a Converse API (ChatBedrockConverse), o `output` pode vir como uma lista
    de blocos de conteúdo — ex.: [{'type': 'text', 'text': '...'}] — em vez de
    string pura. Também removemos blocos de raciocínio <thinking>...</thinking>
    que alguns modelos emitem.
    """
    if isinstance(output, list):
        text = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in output
        )
    elif isinstance(output, str):
        text = output
    else:
        text = str(output)

    text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL)
    return text.strip() or "Não consegui processar sua pergunta."


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
    try:
        await init_db_pool()
        logger.info("Pool de DB inicializado com sucesso")
    except Exception as e:
        logger.error(f"Falha ao inicializar pool de DB: {e} — serviço iniciará sem acesso ao banco")

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

        bot_response = result.get("output", "")
        bot_response = _normalize_output(bot_response)

        # early_stopping_method="generate" já produz uma resposta, mas por segurança:
        if not bot_response or "agent stopped" in bot_response.lower() or "iteration limit" in bot_response.lower():
            bot_response = "Não consegui obter os dados a tempo. Tente perguntas como: 'quantos pedidos hoje?', 'entregadores disponíveis?' ou 'resumo operacional'."

    except Exception as e:
        logger.exception(f"Erro no agente")
        err = str(e).lower()
        if "max iterations" in err or "iteration limit" in err:
            bot_response = "Não consegui completar a consulta dentro do tempo limite. Tente uma pergunta mais específica, como 'quantos pedidos hoje?' ou 'entregadores disponíveis'."
        elif "accessdenied" in err or "is not authorized" in err or "access denied" in err:
            bot_response = "Erro de permissão no Bedrock (AccessDeniedException). Adicione a policy AmazonBedrockFullAccess ao IAM user usado nas credenciais."
        elif "resourcenotfound" in err or "model not found" in err or "unable to locate" in err or "no such" in err:
            bot_response = "Modelo não encontrado no Bedrock. Acesse AWS Console → Bedrock → Model access e habilite 'Amazon Nova Micro'."
        elif "validationexception" in err or "validation error" in err:
            bot_response = f"Erro de validação no Bedrock: {str(e)[:300]}"
        elif "throttl" in err:
            bot_response = "Muitas requisições ao Bedrock. Aguarde alguns segundos e tente novamente."
        elif "connect" in err or "timeout" in err or "timed out" in err:
            bot_response = "Timeout ao conectar ao Bedrock. Verifique a conectividade da VPC/NAT Gateway."
        else:
            bot_response = f"Erro Bedrock: {str(e)[:300]}"

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
