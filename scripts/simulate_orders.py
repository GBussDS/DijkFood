#!/usr/bin/env python3
"""
DijkFood — Simulador de Pedidos
Gera dados realistas com Faker e os envia pelas APIs públicas (via ALB),
simulando o ciclo completo de pedidos em tempo real.

Uso:
  pip install faker httpx
  python scripts/simulate_orders.py --alb http://<alb-url>
  python scripts/simulate_orders.py --alb http://<alb-url> --interval 5 --orders 50
"""

import argparse
import asyncio
import random
import sys
import time
from datetime import datetime

import httpx
from faker import Faker

fake = Faker("pt_BR")

# Bairros de São Paulo com coordenadas aproximadas
SP_NEIGHBORHOODS = [
    (-23.5489, -46.6388),  # Centro
    (-23.5613, -46.6564),  # Pinheiros
    (-23.5534, -46.6621),  # Vila Madalena
    (-23.5570, -46.6502),  # Consolação
    (-23.5601, -46.6442),  # Jardins
    (-23.5455, -46.6310),  # Itaim Bibi
    (-23.5628, -46.6518),  # Moema
    (-23.5510, -46.6475),  # Bela Vista
    (-23.5478, -46.6540),  # Liberdade
    (-23.5592, -46.6395),  # Paraíso
    (-23.5640, -46.6530),  # Aclimação
    (-23.5502, -46.6401),  # Santo André (região)
]

CUISINE_TYPES = ["Japonesa", "Brasileira", "Italiana", "Americana", "Mexicana",
                 "Chinesa", "Árabe", "Francesa", "Indiana", "Vegana"]

VEHICLE_TYPES = ["Moto", "Bike", "Carro"]

# Ciclo completo de status de um pedido
STATUS_CHAIN = [
    "PREPARING",
    "READY_FOR_PICKUP",
    "PICKED_UP",
    "IN_TRANSIT",
    "DELIVERED",
]


def _coord_with_jitter(lat, lon, radius=0.015):
    return (
        lat + random.uniform(-radius, radius),
        lon + random.uniform(-radius, radius),
    )


async def seed_entities(client: httpx.AsyncClient, base: str, n_restaurants=10, n_customers=20, n_couriers=8):
    """Cria restaurantes, clientes e entregadores se o banco estiver vazio."""

    print("→ Verificando entidades existentes...")

    couriers_resp = await client.get(f"{base}/api/couriers", timeout=10)
    restaurants_resp = await client.get(f"{base}/api/restaurants", timeout=10)

    existing_couriers = couriers_resp.json() if couriers_resp.status_code == 200 else []
    existing_restaurants = restaurants_resp.json() if restaurants_resp.status_code == 200 else []

    restaurants = [r["id"] for r in existing_restaurants]
    couriers = [c["id"] for c in existing_couriers]
    customers = []

    # Restaurantes
    if len(existing_restaurants) < n_restaurants:
        to_create = n_restaurants - len(existing_restaurants)
        print(f"  Criando {to_create} restaurantes...")
        for _ in range(to_create):
            lat, lon = random.choice(SP_NEIGHBORHOODS)
            lat, lon = _coord_with_jitter(lat, lon, 0.01)
            resp = await client.post(f"{base}/api/restaurants", json={
                "name": fake.company() + " " + random.choice(["Grill", "Kitchen", "Bistro", "Delivery", "Express"]),
                "cuisine_type": random.choice(CUISINE_TYPES),
                "latitude": lat,
                "longitude": lon,
            }, timeout=10)
            if resp.status_code == 201:
                restaurants.append(resp.json()["id"])

    # Entregadores
    if len(existing_couriers) < n_couriers:
        to_create = n_couriers - len(existing_couriers)
        print(f"  Criando {to_create} entregadores...")
        for _ in range(to_create):
            lat, lon = random.choice(SP_NEIGHBORHOODS)
            lat, lon = _coord_with_jitter(lat, lon, 0.02)
            resp = await client.post(f"{base}/api/couriers", json={
                "name": fake.name(),
                "vehicle_type": random.choice(VEHICLE_TYPES),
                "latitude": lat,
                "longitude": lon,
            }, timeout=10)
            if resp.status_code == 201:
                couriers.append(resp.json()["id"])

    # Clientes (criados on-the-fly; mantemos um pool em memória)
    print(f"  Criando {n_customers} clientes iniciais...")
    for _ in range(n_customers):
        lat, lon = random.choice(SP_NEIGHBORHOODS)
        lat, lon = _coord_with_jitter(lat, lon, 0.015)
        resp = await client.post(f"{base}/api/customers", json={
            "name": fake.name(),
            "email": fake.unique.email(),
            "phone": fake.phone_number()[:20],
            "latitude": lat,
            "longitude": lon,
        }, timeout=10)
        if resp.status_code == 201:
            customers.append(resp.json()["id"])

    print(f"  Entidades: {len(restaurants)} restaurantes | {len(customers)} clientes | {len(couriers)} entregadores")
    return restaurants, customers, couriers


async def create_order(client: httpx.AsyncClient, base: str, customer_id: str, restaurant_id: str) -> str | None:
    resp = await client.post(f"{base}/api/orders", json={
        "customer_id": customer_id,
        "restaurant_id": restaurant_id,
        "items": [
            {"name": fake.word().capitalize(), "qty": random.randint(1, 3), "price": round(random.uniform(15, 80), 2)}
            for _ in range(random.randint(1, 4))
        ],
    }, timeout=30)

    if resp.status_code == 201:
        return resp.json()["order_id"]
    return None


async def advance_order(client: httpx.AsyncClient, base: str, order_id: str, delay_between: float = 8.0):
    """Avança um pedido pelo ciclo completo de status com delays entre transições."""
    for status in STATUS_CHAIN:
        await asyncio.sleep(delay_between + random.uniform(-2, 4))
        resp = await client.patch(
            f"{base}/api/orders/{order_id}/status",
            json={"status": status},
            timeout=10,
        )
        if resp.status_code != 200:
            break


async def simulation_loop(base: str, interval: float, max_orders: int, delay_status: float):
    """Loop principal: cria pedidos periodicamente e avança seu ciclo em background."""

    async with httpx.AsyncClient() as client:
        # Seed inicial
        restaurants, customers, couriers = await seed_entities(client, base)

        if not restaurants or not customers:
            print("ERRO: sem restaurantes ou clientes disponíveis. Encerrando.")
            sys.exit(1)

        orders_created = 0
        print(f"\n✓ Iniciando simulação — novo pedido a cada {interval}s\n")
        print(f"{'Hora':10} {'Pedido':38} {'Status':12}")
        print("-" * 65)

        pending_tasks = set()

        while max_orders == 0 or orders_created < max_orders:
            # Criar novo cliente ocasionalmente para variar o pool
            if random.random() < 0.15:
                lat, lon = random.choice(SP_NEIGHBORHOODS)
                lat, lon = _coord_with_jitter(lat, lon)
                resp = await client.post(f"{base}/api/customers", json={
                    "name": fake.name(),
                    "email": fake.unique.email(),
                    "phone": fake.phone_number()[:20],
                    "latitude": lat,
                    "longitude": lon,
                }, timeout=10)
                if resp.status_code == 201:
                    customers.append(resp.json()["id"])

            customer_id = random.choice(customers)
            restaurant_id = random.choice(restaurants)

            order_id = await create_order(client, base, customer_id, restaurant_id)
            now = datetime.now().strftime("%H:%M:%S")

            if order_id:
                orders_created += 1
                short_id = order_id[:8] + "..."
                print(f"{now:10} {short_id:38} CONFIRMED  (#{orders_created})")

                task = asyncio.create_task(advance_order(client, base, order_id, delay_status))
                pending_tasks.add(task)
                task.add_done_callback(pending_tasks.discard)
            else:
                print(f"{now:10} {'— falha ao criar pedido (sem entregador disponível?)':50}")

            await asyncio.sleep(interval)

        # Aguarda pedidos em andamento terminarem
        if pending_tasks:
            print(f"\nAguardando {len(pending_tasks)} pedidos finalizarem...")
            await asyncio.gather(*pending_tasks, return_exceptions=True)

        print(f"\n✓ Simulação concluída: {orders_created} pedidos criados.")


def main():
    parser = argparse.ArgumentParser(description="DijkFood — Simulador de Pedidos")
    parser.add_argument("--alb", required=True, help="URL base do ALB, ex: http://dijkfood-alb-....elb.amazonaws.com")
    parser.add_argument("--interval", type=float, default=10.0, help="Segundos entre criação de pedidos (default: 10)")
    parser.add_argument("--orders", type=int, default=0, help="Número máximo de pedidos (0 = infinito)")
    parser.add_argument("--delay-status", type=float, default=8.0, help="Segundos entre transições de status (default: 8)")
    args = parser.parse_args()

    base = args.alb.rstrip("/")
    print(f"DijkFood Simulator → {base}")
    print(f"Intervalo entre pedidos: {args.interval}s | Delay status: {args.delay_status}s")
    if args.orders:
        print(f"Limite: {args.orders} pedidos")
    print()

    try:
        asyncio.run(simulation_loop(base, args.interval, args.orders, args.delay_status))
    except KeyboardInterrupt:
        print("\n\nSimulação interrompida pelo usuário.")


if __name__ == "__main__":
    main()
