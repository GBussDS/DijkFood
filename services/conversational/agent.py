"""
DijkFood — Agente Conversacional: LangChain Agent Setup
Configura o agente com ChatBedrock (Claude 3 Sonnet) e tools para consultar dados.
"""
import asyncio
import logging
import os

from langchain_aws import ChatBedrock
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain.tools import Tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

import tools as _tools_module
from tools import (
    query_athena,
    query_orders_db,
    get_courier_position,
    get_prediction,
    detect_anomalies,
)

logger = logging.getLogger(__name__)

# Configuração do Bedrock — usa profile 'bedrock' para acesso ao modelo
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
BEDROCK_PROFILE = os.environ.get("BEDROCK_PROFILE", "bedrock")
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-micro-v1:0")

# Wrappers síncronos para tools async
def _query_orders_sync(query_description: str) -> str:
    """Wrapper síncrono para query_orders_db.

    O pool asyncpg é ligado ao event loop principal (uvicorn). run_coroutine_threadsafe
    agenda a coroutine nesse loop e devolve um Future que podemos aguardar da thread
    da tool sem criar um novo event loop (o que causaria deadlock com o pool asyncpg).
    O loop é capturado em tools._main_loop durante o startup do FastAPI.
    """
    loop = _tools_module._main_loop
    if loop is None or not loop.is_running():
        return "Database não disponível (loop principal ausente)"
    future = asyncio.run_coroutine_threadsafe(query_orders_db(query_description), loop)
    return future.result(timeout=30)


def create_agent():
    """Cria e retorna o AgentExecutor com LangChain + Bedrock."""

    # 1. Configurar modelo Bedrock
    #
    # IMPORTANTE: as credenciais precisam ir DIRETO nos campos do ChatBedrock, e
    # NÃO via `client=`. Para tool calling, o ChatBedrock recria internamente um
    # ChatBedrockConverse (operação ConverseStream) propagando apenas os campos
    # aws_access_key_id/aws_secret_access_key/aws_session_token — ele IGNORA o
    # `client` que passarmos. Se as credenciais não forem passadas nesses campos,
    # ele cai na cadeia de credenciais padrão (a LabRole da task no ECS), que NÃO
    # tem permissão de bedrock:InvokeModel, resultando em AccessDeniedException.
    bedrock_access_key = os.environ.get("BEDROCK_AWS_ACCESS_KEY_ID")
    bedrock_secret_key = os.environ.get("BEDROCK_AWS_SECRET_ACCESS_KEY")

    if bedrock_access_key and bedrock_secret_key:
        # Produção (ECS): credenciais explícitas da conta com acesso ao Bedrock.
        llm = ChatBedrock(
            model_id=MODEL_ID,
            region_name=AWS_REGION,
            aws_access_key_id=bedrock_access_key,
            aws_secret_access_key=bedrock_secret_key,
            aws_session_token=os.environ.get("BEDROCK_AWS_SESSION_TOKEN") or None,
            model_kwargs={"temperature": 0.1, "max_tokens": 2048},
        )
        logger.info("ChatBedrock criado com credenciais explícitas (env vars)")
    else:
        # Dev local: usa o profile nomeado do ~/.aws/credentials.
        llm = ChatBedrock(
            model_id=MODEL_ID,
            region_name=AWS_REGION,
            credentials_profile_name=BEDROCK_PROFILE,
            model_kwargs={"temperature": 0.1, "max_tokens": 2048},
        )
        logger.info(f"ChatBedrock criado com profile '{BEDROCK_PROFILE}'")

    # 2. Definir tools que o agente pode usar
    tools = [
        Tool(
            name="query_analytics",
            func=query_athena,
            description=(
                "Executa uma consulta SQL no Amazon Athena sobre o data lake de eventos. "
                "Use para dados históricos e agregados: volume de pedidos por hora, tempos médios de entrega, "
                "distribuição por região, rankings de restaurantes, heatmaps de demanda. "
                "A tabela se chama 'events' no database 'dijkfood_analytics'. "
                "Colunas: event_type, order_id, customer_id, restaurant_id, courier_id, "
                "old_status, new_status, latitude, longitude, estimated_time, user_message, "
                "bot_response, timestamp. "
                "Partições: year, month, day, hour."
            )
        ),
        Tool(
            name="query_operations",
            func=_query_orders_sync,
            description=(
                "Consulta dados operacionais em tempo real do banco de pedidos (PostgreSQL). "
                "Use para: status de pedidos específicos, contagens atuais, dados de clientes/restaurantes, "
                "entregadores disponíveis, tempo médio de entrega. "
                "Parâmetro: descrição em linguagem natural da consulta desejada."
            )
        ),
        Tool(
            name="get_position",
            func=get_courier_position,
            description=(
                "Obtém a posição geográfica atual (latitude/longitude) de um entregador. "
                "Parâmetro: UUID do entregador."
            )
        ),
        Tool(
            name="get_prediction",
            func=get_prediction,
            description=(
                "Obtém predições do modelo de ML: 'delivery_time' para tempo de entrega "
                "estimado, 'demand' para previsão de demanda por região e horário."
            )
        ),
        Tool(
            name="check_anomalies",
            func=lambda _: detect_anomalies(),
            description=(
                "Verifica anomalias operacionais ativas no momento: "
                "tempos de entrega fora do normal, regiões sem entregadores, "
                "picos de pedidos, atrasos em transições de status."
            )
        ),
    ]

    # 3. Prompt do agente
    prompt = ChatPromptTemplate.from_messages([
        ("system", """Você é um assistente operacional da plataforma DijkFood.
Você tem acesso a dados operacionais em tempo real e dados analíticos históricos.

Schema do data lake (Athena - database: dijkfood_analytics):
- tabela 'events': event_type, order_id, customer_id, restaurant_id, courier_id,
  old_status, new_status, latitude, longitude, timestamp, estimated_time
- event_types: ORDER_CREATED, STATUS_CHANGED, POSITION_UPDATE

Schema operacional (PostgreSQL):
- orders: id, customer_id, restaurant_id, courier_id, items, status, route,
  estimated_time, created_at, updated_at
- customers: id, name, email, phone, latitude, longitude
- restaurants: id, name, cuisine_type, latitude, longitude
- couriers: id, name, vehicle_type, latitude, longitude, status
- order_events: id, order_id, status, timestamp

Sempre responda em português. Formate números adequadamente.
Para saudações, perguntas sobre suas capacidades ou perguntas conceituais simples,
responda diretamente SEM usar ferramentas.
Só use ferramentas quando precisar de dados reais do sistema (pedidos, entregadores, etc.).
Para dados históricos/agregados use query_analytics (Athena).
Para dados em tempo real use query_operations (PostgreSQL).
Chame cada ferramenta NO MÁXIMO UMA VEZ por resposta. Se a ferramenta retornar erro,
responda com o que você sabe sem tentar novamente.
"""),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])

    # 4. Criar agente
    agent = create_tool_calling_agent(llm, tools, prompt)
    executor = AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=True,
        max_iterations=8,
        early_stopping_method="generate",  # gera resposta final em vez de retornar o erro
        handle_parsing_errors=True,
    )

    logger.info("Agente conversacional criado com sucesso")
    return executor
