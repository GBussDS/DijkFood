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
    """Wrapper síncrono para query_orders_db."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
        
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, query_orders_db(query_description))
            return future.result()
    return asyncio.run(query_orders_db(query_description))


def create_agent():
    """Cria e retorna o AgentExecutor com LangChain + Bedrock."""

    # 1. Configurar modelo Bedrock
    import boto3
    from botocore.exceptions import ProfileNotFound

    try:
        # Tenta usar o profile local (quando rodando docker-compose ou python local)
        session = boto3.Session(profile_name=BEDROCK_PROFILE)
        bedrock_client = session.client("bedrock-runtime", region_name=AWS_REGION)
    except ProfileNotFound:
        # Quando rodando no ECS da AWS Academy (que não tem ~/.aws/credentials)
        # Lê credenciais do bedrock via variáveis de ambiente
        bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=AWS_REGION,
            aws_access_key_id=os.environ.get("BEDROCK_AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("BEDROCK_AWS_SECRET_ACCESS_KEY")
        )

    llm = ChatBedrock(
        model_id=MODEL_ID,
        region_name=AWS_REGION,
        model_kwargs={"temperature": 0.1, "max_tokens": 2048},
        client=bedrock_client
    )

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
            func=detect_anomalies,
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
Use as ferramentas disponíveis para buscar dados antes de responder.
Para dados históricos/agregados use query_analytics (Athena).
Para dados em tempo real use query_operations (PostgreSQL) ou get_position (DynamoDB).
"""),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])

    # 4. Criar agente
    agent = create_tool_calling_agent(llm, tools, prompt)
    executor = AgentExecutor(agent=agent, tools=tools, verbose=True, max_iterations=5)

    logger.info("Agente conversacional criado com sucesso")
    return executor
