#!/usr/bin/env python3
"""
DijkFood A2 — Simulador de Carga
Simula cenários de uso da plataforma com medição de latências e throughput.

Cenários base (A1):
  - normal:   10 pedidos/s por 5min
  - peak:     50 pedidos/s por 5min
  - special:  200 pedidos/s por 5min

Cenários adicionais (A2):
  - regional:  80% pedidos em uma região
  - popular:   50% pedidos em 2 restaurantes
  - scarcity:  10% entregadores disponíveis
  - chat:      50 perguntas/s ao agente

Uso:
  python simulator.py --url http://ALB_DNS --scenario normal --duration 300
  python simulator.py --url http://ALB_DNS --scenario peak --duration 300 --chat-qps 10
"""
import argparse
import asyncio
import json
import logging
import random
import statistics
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from uuid import UUID, uuid4

import aiohttp

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("simulator")


@dataclass
class SimulationConfig:
    base_url: str
    orders_per_second: int
    duration_seconds: int
    num_customers: int = 50
    num_restaurants: int = 20
    num_couriers: int = 150  # 3x clientes
    scenario: str = "normal"
    chat_queries_per_second: int = 0
    anomaly_injection: bool = False


@dataclass
class SimulationResults:
    latencies: Dict[str, List[float]] = field(default_factory=lambda: {
        "create_order": [],
        "update_status": [],
        "get_status": [],
        "update_position": [],
        "chat": [],
        "predict": [],
    })
    errors: Dict[str, int] = field(default_factory=lambda: {
        "create_order": 0,
        "update_status": 0,
        "get_status": 0,
        "update_position": 0,
        "chat": 0,
        "predict": 0,
    })
    total_orders_created: int = 0
    total_orders_completed: int = 0


SCENARIO_CONFIGS = {
    "normal": {"orders_per_second": 10, "duration_seconds": 300},
    "peak": {"orders_per_second": 50, "duration_seconds": 300},
    "special": {"orders_per_second": 200, "duration_seconds": 300},
    "regional": {"orders_per_second": 50, "duration_seconds": 300},
    "popular": {"orders_per_second": 50, "duration_seconds": 300},
    "scarcity": {"orders_per_second": 30, "duration_seconds": 300},
    "chat": {"orders_per_second": 10, "duration_seconds": 300, "chat_queries_per_second": 50},
}


class LoadSimulator:
    def __init__(self, config: SimulationConfig):
        self.config = config
        self.results = SimulationResults()
        self.customer_ids: List[str] = []
        self.restaurant_ids: List[str] = []
        self.courier_ids: List[str] = []
        # Regiões de SP para cenário regional
        self.sp_regions = {
            "centro": (-23.55, -46.63),
            "zona_sul": (-23.65, -46.65),
            "zona_norte": (-23.45, -46.62),
            "zona_leste": (-23.54, -46.50),
            "zona_oeste": (-23.55, -46.75),
        }

    async def run(self):
        """Executa simulação completa."""
        logger.info(f"=== Simulação: {self.config.scenario} ===")
        logger.info(f"URL: {self.config.base_url}")
        logger.info(f"Taxa: {self.config.orders_per_second} pedidos/s")
        logger.info(f"Duração: {self.config.duration_seconds}s")

        connector = aiohttp.TCPConnector(limit=500)
        timeout = aiohttp.ClientTimeout(total=30)

        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            # Fase 1: Seed data
            logger.info("\n📋 Fase 1: Seeding dados iniciais...")
            await self.phase_seed(session)

            # Fase 2: Carga
            logger.info(f"\n🚀 Fase 2: Gerando carga ({self.config.orders_per_second} pedidos/s)...")
            await self.phase_load(session)

        # Fase 3: Relatório
        logger.info("\n📊 Fase 3: Relatório")
        self.report()

    async def phase_seed(self, session):
        """Popular com entidades iniciais."""
        # Criar clientes
        for i in range(self.config.num_customers):
            lat, lon = self._random_sp_location()
            result = await self._request(session, "POST", "/api/customers", {
                "name": f"Customer_{i}",
                "email": f"customer{i}_{uuid4().hex[:8]}@dijkfood.test",
                "phone": f"1199{random.randint(1000000, 9999999)}",
                "latitude": lat,
                "longitude": lon,
            })
            if result and "id" in result:
                self.customer_ids.append(result["id"])

        logger.info(f"  ✅ {len(self.customer_ids)} clientes criados")

        # Criar restaurantes
        for i in range(self.config.num_restaurants):
            lat, lon = self._random_sp_location(radius=0.08)
            result = await self._request(session, "POST", "/api/restaurants", {
                "name": f"Restaurant_{i}",
                "cuisine_type": random.choice(["Italiana", "Japonesa", "Brasileira", "Mexicana", "Árabe"]),
                "latitude": lat,
                "longitude": lon,
            })
            if result and "id" in result:
                self.restaurant_ids.append(result["id"])

        logger.info(f"  ✅ {len(self.restaurant_ids)} restaurantes criados")

        # Criar entregadores
        for i in range(self.config.num_couriers):
            lat, lon = self._random_sp_location()
            result = await self._request(session, "POST", "/api/couriers", {
                "name": f"Courier_{i}",
                "vehicle_type": random.choice(["moto", "bicicleta"]),
                "latitude": lat,
                "longitude": lon,
            })
            if result and "id" in result:
                self.courier_ids.append(result["id"])

        logger.info(f"  ✅ {len(self.courier_ids)} entregadores criados")

    async def phase_load(self, session):
        """Gerar carga na taxa configurada."""
        if not self.customer_ids or not self.restaurant_ids:
            logger.error("Sem entidades para simular. Abortando.")
            return

        interval = 1.0 / max(self.config.orders_per_second, 1)
        end_time = time.time() + self.config.duration_seconds
        tasks = []
        order_count = 0

        while time.time() < end_time:
            # Criar pedido
            task = asyncio.create_task(self._simulate_order_lifecycle(session))
            tasks.append(task)
            order_count += 1

            # Chat queries (se configurado)
            if self.config.chat_queries_per_second > 0:
                chat_interval = 1.0 / self.config.chat_queries_per_second
                if random.random() < chat_interval * self.config.orders_per_second:
                    chat_task = asyncio.create_task(self._simulate_chat(session))
                    tasks.append(chat_task)

            # Logging de progresso
            if order_count % 100 == 0:
                elapsed = self.config.duration_seconds - (end_time - time.time())
                logger.info(f"  📦 {order_count} pedidos iniciados ({elapsed:.0f}s)")

            await asyncio.sleep(interval)

        # Aguardar todas as tasks completarem
        logger.info(f"  Aguardando {len(tasks)} tasks completarem...")
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _simulate_order_lifecycle(self, session):
        """Simula ciclo completo de um pedido."""
        try:
            customer_id = random.choice(self.customer_ids)
            restaurant_id = self._pick_restaurant()

            # 1. Criar pedido
            order = await self._request(session, "POST", "/api/orders", {
                "customer_id": customer_id,
                "restaurant_id": restaurant_id,
                "items": [{"name": random.choice(["Hamburguer", "Pizza", "Sushi", "Açaí"]), "qty": 1}],
            }, metric="create_order")

            if not order or "order_id" not in order:
                return

            self.results.total_orders_created += 1
            order_id = order["order_id"]
            courier_id = order.get("courier_id", "")

            # 2. Transições de status
            statuses = ["PREPARING", "READY_FOR_PICKUP", "PICKED_UP", "IN_TRANSIT", "DELIVERED"]
            for status in statuses:
                await asyncio.sleep(random.uniform(0.3, 1.5))

                await self._request(session, "PATCH", f"/api/orders/{order_id}/status",
                                    {"status": status}, metric="update_status")

                # Posições durante transporte
                if status in ["PICKED_UP", "IN_TRANSIT"] and courier_id:
                    for _ in range(5):
                        lat, lon = self._random_sp_location(radius=0.05)
                        await self._request(session, "POST", "/api/positions", {
                            "courier_id": courier_id,
                            "order_id": order_id,
                            "latitude": lat,
                            "longitude": lon,
                        }, metric="update_position")
                        await asyncio.sleep(0.1)

                # Consultar status
                await self._request(session, "GET", f"/api/orders/{order_id}", metric="get_status")

            self.results.total_orders_completed += 1

        except Exception as e:
            logger.debug(f"Erro no lifecycle: {e}")

    async def _simulate_chat(self, session):
        """Simula pergunta ao agente conversacional."""
        questions = [
            "Qual o tempo médio de entrega hoje?",
            "Quantos pedidos nas últimas 2 horas?",
            "Tem alguma anomalia operacional agora?",
            "Quais os 5 restaurantes mais pedidos?",
            "Previsão de demanda para as próximas 2 horas?",
            "Quantos entregadores estão disponíveis?",
            "Qual foi o pedido mais demorado hoje?",
        ]
        await self._request(session, "POST", "/api/chat",
                            {"message": random.choice(questions)}, metric="chat")

    async def _request(self, session, method, path, body=None, metric=None):
        """Executa request com medição de latência."""
        start = time.monotonic()
        try:
            url = f"{self.config.base_url}{path}"
            if method == "GET":
                async with session.get(url) as resp:
                    result = await resp.json()
            elif method == "POST":
                async with session.post(url, json=body) as resp:
                    result = await resp.json()
            elif method == "PATCH":
                async with session.patch(url, json=body) as resp:
                    result = await resp.json()
            else:
                return None

            latency_ms = (time.monotonic() - start) * 1000
            if metric:
                self.results.latencies[metric].append(latency_ms)
            return result

        except Exception as e:
            if metric:
                self.results.errors[metric] = self.results.errors.get(metric, 0) + 1
            return None

    def _pick_restaurant(self):
        """Escolhe restaurante com base no cenário."""
        if self.config.scenario == "popular" and len(self.restaurant_ids) >= 2:
            # 50% dos pedidos em 2 restaurantes
            if random.random() < 0.5:
                return random.choice(self.restaurant_ids[:2])
        elif self.config.scenario == "regional":
            # 80% na mesma região (primeiros restaurantes)
            if random.random() < 0.8 and len(self.restaurant_ids) >= 5:
                return random.choice(self.restaurant_ids[:5])

        return random.choice(self.restaurant_ids)

    def _random_sp_location(self, radius=0.1):
        """Gera coordenada aleatória na região de SP."""
        center_lat, center_lon = -23.55, -46.63
        lat = center_lat + random.uniform(-radius, radius)
        lon = center_lon + random.uniform(-radius, radius)
        return round(lat, 6), round(lon, 6)

    def report(self):
        """Gera relatório de latências e throughput."""
        print("\n" + "=" * 70)
        print(f"  RESULTADOS — Cenário: {self.config.scenario}")
        print(f"  Taxa configurada: {self.config.orders_per_second} pedidos/s")
        print(f"  Duração: {self.config.duration_seconds}s")
        print(f"  Pedidos criados: {self.results.total_orders_created}")
        print(f"  Pedidos completados: {self.results.total_orders_completed}")
        print("=" * 70)

        for operation, times in self.results.latencies.items():
            if not times:
                continue
            errors = self.results.errors.get(operation, 0)
            sorted_times = sorted(times)
            n = len(sorted_times)

            print(f"\n  * {operation}:")
            print(f"     Total requests:  {n}")
            print(f"     Erros:           {errors}")
            print(f"     P50 (median):    {sorted_times[int(n * 0.50)]:.1f}ms")
            print(f"     P95:             {sorted_times[int(n * 0.95)]:.1f}ms")
            print(f"     P99:             {sorted_times[min(int(n * 0.99), n - 1)]:.1f}ms")
            print(f"     Max:             {max(sorted_times):.1f}ms")
            print(f"     Mean:            {statistics.mean(sorted_times):.1f}ms")
            print(f"     Throughput:      {n / self.config.duration_seconds:.1f} req/s")

        print("\n" + "=" * 70)

        # Verificar SLA: P95 < 500ms
        for op in ["create_order", "get_status", "update_status"]:
            times = self.results.latencies.get(op, [])
            if times:
                p95 = sorted(times)[int(len(times) * 0.95)]
                status = "[OK]" if p95 < 500 else "[FAIL]"
                print(f"  {status} SLA P95 < 500ms para {op}: {p95:.1f}ms")

        print("=" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser(description="DijkFood Load Simulator")
    parser.add_argument("--url", required=True, help="Base URL do ALB (ex: http://dijkfood-alb-xxx.amazonaws.com)")
    parser.add_argument("--scenario", default="normal", choices=list(SCENARIO_CONFIGS.keys()))
    parser.add_argument("--duration", type=int, default=None, help="Duração em segundos (override)")
    parser.add_argument("--rate", type=int, default=None, help="Pedidos/s (override)")
    parser.add_argument("--chat-qps", type=int, default=0, help="Queries de chat por segundo")
    parser.add_argument("--customers", type=int, default=50)
    parser.add_argument("--restaurants", type=int, default=20)
    parser.add_argument("--couriers", type=int, default=150)
    args = parser.parse_args()

    # Aplicar config do cenário
    scenario_cfg = SCENARIO_CONFIGS.get(args.scenario, SCENARIO_CONFIGS["normal"])

    config = SimulationConfig(
        base_url=args.url.rstrip("/"),
        orders_per_second=args.rate or scenario_cfg.get("orders_per_second", 10),
        duration_seconds=args.duration or scenario_cfg.get("duration_seconds", 300),
        num_customers=args.customers,
        num_restaurants=args.restaurants,
        num_couriers=args.couriers,
        scenario=args.scenario,
        chat_queries_per_second=args.chat_qps or scenario_cfg.get("chat_queries_per_second", 0),
    )

    simulator = LoadSimulator(config)
    asyncio.run(simulator.run())


if __name__ == "__main__":
    main()
