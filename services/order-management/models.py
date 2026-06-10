"""
DijkFood — Order Management: Pydantic Models
"""
from pydantic import BaseModel, Field
from typing import List, Optional
from uuid import UUID
from datetime import datetime


# === Customer ===
class CreateCustomerRequest(BaseModel):
    name: str
    email: str
    phone: str
    latitude: float
    longitude: float


class CustomerResponse(BaseModel):
    id: UUID
    name: str
    email: str
    phone: str
    latitude: float
    longitude: float
    created_at: Optional[datetime] = None


# === Restaurant ===
class CreateRestaurantRequest(BaseModel):
    name: str
    cuisine_type: str
    latitude: float
    longitude: float


class RestaurantResponse(BaseModel):
    id: UUID
    name: str
    cuisine_type: str
    latitude: float
    longitude: float
    created_at: Optional[datetime] = None


# === Courier ===
class CreateCourierRequest(BaseModel):
    name: str
    vehicle_type: str
    latitude: float
    longitude: float


class CourierResponse(BaseModel):
    id: UUID
    name: str
    vehicle_type: str
    latitude: float
    longitude: float
    status: str
    created_at: Optional[datetime] = None


# === Order ===
class OrderResponse(BaseModel):
    id: UUID
    customer_id: UUID
    restaurant_id: UUID
    courier_id: Optional[UUID] = None
    items: Optional[str] = None
    status: str
    route: Optional[str] = None
    estimated_time: Optional[float] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class UpdateStatusRequest(BaseModel):
    status: str


class OrderEventResponse(BaseModel):
    id: UUID
    order_id: UUID
    status: str
    timestamp: Optional[datetime] = None


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "order-management"
