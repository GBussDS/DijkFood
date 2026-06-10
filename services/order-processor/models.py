"""
DijkFood — Order Processor: Pydantic Models
"""
from pydantic import BaseModel, Field
from typing import List, Optional
from uuid import UUID


class OrderItem(BaseModel):
    name: str
    qty: int = 1
    price: Optional[float] = None


class CreateOrderRequest(BaseModel):
    customer_id: UUID
    restaurant_id: UUID
    items: List[OrderItem]


class CreateOrderResponse(BaseModel):
    order_id: UUID
    courier_id: UUID
    route: List[List[float]]
    estimated_delivery_time: float


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "order-processor"
    graph_loaded: bool = False
