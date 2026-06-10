"""
DijkFood — Position Tracker: Route Handlers
Gerencia posições de entregadores via DynamoDB + emissão de eventos no Kinesis.
"""
import json
import logging
import os
import time
from datetime import datetime
from uuid import UUID

import boto3
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
KINESIS_STREAM = os.environ.get("KINESIS_STREAM", "dijkfood-events")
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "courier_positions")


class PositionUpdate(BaseModel):
    courier_id: UUID
    order_id: UUID
    latitude: float
    longitude: float


class PositionResponse(BaseModel):
    courier_id: str
    latitude: float
    longitude: float
    order_id: str
    timestamp: str


def get_dynamodb():
    return boto3.client("dynamodb", region_name=AWS_REGION)


def get_kinesis():
    return boto3.client("kinesis", region_name=AWS_REGION)


@router.post("/api/positions")
async def update_position(request: PositionUpdate):
    """
    Recebe e armazena posição do entregador.
    1. Escreve no DynamoDB (estado atual — hot data)
    2. Emite evento POSITION_UPDATE para Kinesis (pipeline analítico)
    """
    dynamodb = get_dynamodb()
    now = datetime.utcnow().isoformat()
    ttl = int(time.time()) + 86400  # 24h

    # 1. Escrever no DynamoDB
    try:
        dynamodb.put_item(
            TableName=DYNAMODB_TABLE,
            Item={
                "courier_id": {"S": str(request.courier_id)},
                "latitude": {"N": str(request.latitude)},
                "longitude": {"N": str(request.longitude)},
                "order_id": {"S": str(request.order_id)},
                "timestamp": {"S": now},
                "ttl": {"N": str(ttl)}
            }
        )
    except Exception as e:
        logger.error(f"Erro ao escrever no DynamoDB: {e}")
        raise HTTPException(status_code=500, detail="Failed to store position")

    # 2. Emitir evento para Kinesis
    try:
        kinesis = get_kinesis()
        kinesis.put_record(
            StreamName=KINESIS_STREAM,
            Data=json.dumps({
                "event_type": "POSITION_UPDATE",
                "courier_id": str(request.courier_id),
                "order_id": str(request.order_id),
                "latitude": request.latitude,
                "longitude": request.longitude,
                "timestamp": now
            }),
            PartitionKey=str(request.courier_id)
        )
    except Exception as e:
        logger.error(f"Falha ao emitir evento Kinesis: {e}")

    return {"status": "ok"}


@router.get("/api/positions/{courier_id}", response_model=PositionResponse)
async def get_position(courier_id: UUID):
    """Retorna a posição atual (última conhecida) de um entregador."""
    dynamodb = get_dynamodb()

    try:
        response = dynamodb.get_item(
            TableName=DYNAMODB_TABLE,
            Key={"courier_id": {"S": str(courier_id)}}
        )
    except Exception as e:
        logger.error(f"Erro ao ler DynamoDB: {e}")
        raise HTTPException(status_code=500, detail="Failed to read position")

    item = response.get("Item")
    if not item:
        raise HTTPException(status_code=404, detail="Position not found for courier")

    return PositionResponse(
        courier_id=item["courier_id"]["S"],
        latitude=float(item["latitude"]["N"]),
        longitude=float(item["longitude"]["N"]),
        order_id=item["order_id"]["S"],
        timestamp=item["timestamp"]["S"]
    )
