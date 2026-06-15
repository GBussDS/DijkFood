#!/usr/bin/env python3
"""
DijkFood — Simulador de Carga
Cenários: 10, 100, 200 pedidos/segundo.
Proporção: 3 entregadores por cliente (3:1 couriers:customers).
SLA: P95 < 500ms para operações de consulta e registro.

Uso:
  pip install httpx faker
  python scripts/load_test.py --alb http://<alb-url> --scenario all
  python scripts/load_test.py --alb http://<alb-url> --scenario 10
  python scripts/load_test.py --alb http://<alb-url> --scenario 100
  python scripts/load_test.py --alb http://<alb-url> --scenario 200
  python scripts/load_test.py --alb http://<alb-url> --scenario all --dashboard http://<dash-url>
"""

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import httpx
from faker import Faker

fake = Faker("pt_BR")

# ============================================================
# CENÁRIOS — proporção fixa de 3 entregadores por cliente
# ============================================================
SCENARIOS: Dict[int, dict] = {
    10: {
        "target_ops": 10,
        "customers": 50,
        "couriers": 150,      # 3 × clientes
        "restaurants": 10,
        "duration_seconds": 60,
        "description": "Carga Leve (10 ped/s)",
    },
    100: {
        "target_ops": 100,
        "customers": 100,
        "couriers": 300,      # 3 × clientes
        "restaurants": 20,
        "duration_seconds": 30,
        "description": "Carga Média (100 ped/s)",
    },
    200: {
        "target_ops": 200,
        "customers": 200,
        "couriers": 600,      # 3 × clientes
        "restaurants": 40,
        "duration_seconds": 20,
        "description": "Carga Alta (200 ped/s)",
    },
}

SP_NEIGHBORHOODS = [
    (-23.5489, -46.6388),
    (-23.5613, -46.6564),
    (-23.5534, -46.6621),
    (-23.5570, -46.6502),
    (-23.5601, -46.6442),
    (-23.5455, -46.6310),
    (-23.5628, -46.6518),
    (-23.5510, -46.6475),
    (-23.5478, -46.6540),
    (-23.5592, -46.6395),
]

CUISINE_TYPES = ["Japonesa", "Brasileira", "Italiana", "Americana", "Mexicana"]
VEHICLE_TYPES = ["Moto", "Bike", "Carro"]
STATUS_CHAIN = ["PREPARING", "READY_FOR_PICKUP", "PICKED_UP", "IN_TRANSIT", "DELIVERED"]

SLA_MS = 500.0


# ============================================================
# HELPERS
# ============================================================
def _jitter(lat: float, lon: float, r: float = 0.015) -> Tuple[float, float]:
    return lat + random.uniform(-r, r), lon + random.uniform(-r, r)


def percentile(data: List[float], p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    idx = (p / 100) * (len(s) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


# ============================================================
# SEEDING — cria entidades com proporção 3:1
# ============================================================
async def seed_entities(
    client: httpx.AsyncClient,
    base: str,
    config: dict,
) -> Tuple[List[str], List[str], List[str]]:
    print(f"\n→ Configurando entidades para {config['description']}...")
    print(
        f"  Clientes: {config['customers']} | "
        f"Entregadores: {config['couriers']} (3:1) | "
        f"Restaurantes: {config['restaurants']}"
    )

    restaurants: List[str] = []
    customers: List[str] = []
    couriers: List[str] = []

    # Reaproveitar entidades existentes
    try:
        r = await client.get(f"{base}/api/restaurants", timeout=15)
        if r.status_code == 200:
            restaurants = [x["id"] for x in r.json()]
        r = await client.get(f"{base}/api/couriers", timeout=15)
        if r.status_code == 200:
            couriers = [x["id"] for x in r.json()]
    except Exception:
        pass

    # Criar restaurantes faltantes
    to_create = max(0, config["restaurants"] - len(restaurants))
    if to_create > 0:
        print(f"  Criando {to_create} restaurantes...")
        tasks = []
        for _ in range(to_create):
            lat, lon = _jitter(*random.choice(SP_NEIGHBORHOODS))
            tasks.append(client.post(f"{base}/api/restaurants", json={
                "name": fake.company() + " " + random.choice(["Grill", "Delivery", "Express"]),
                "cuisine_type": random.choice(CUISINE_TYPES),
                "latitude": lat,
                "longitude": lon,
            }, timeout=15))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for resp in results:
            if not isinstance(resp, Exception) and resp.status_code == 201:
                restaurants.append(resp.json()["id"])

    # Criar entregadores em lotes (pool HTTP é limitado)
    to_create = max(0, config["couriers"] - len(couriers))
    if to_create > 0:
        print(f"  Criando {to_create} entregadores (proporção 3:1)...")
        batch_size = 50
        for i in range(0, to_create, batch_size):
            batch = min(batch_size, to_create - i)
            tasks = []
            for _ in range(batch):
                lat, lon = _jitter(*random.choice(SP_NEIGHBORHOODS), r=0.02)
                tasks.append(client.post(f"{base}/api/couriers", json={
                    "name": fake.name(),
                    "vehicle_type": random.choice(VEHICLE_TYPES),
                    "latitude": lat,
                    "longitude": lon,
                }, timeout=15))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for resp in results:
                if not isinstance(resp, Exception) and resp.status_code == 201:
                    couriers.append(resp.json()["id"])
            done = min(i + batch, to_create)
            print(f"    {done}/{to_create} entregadores...", end="\r", flush=True)
        print()

    # Criar clientes (sempre novos para ter emails únicos)
    print(f"  Criando {config['customers']} clientes...")
    tasks = []
    for _ in range(config["customers"]):
        lat, lon = _jitter(*random.choice(SP_NEIGHBORHOODS))
        tasks.append(client.post(f"{base}/api/customers", json={
            "name": fake.name(),
            "email": fake.unique.email(),
            "phone": fake.phone_number()[:20],
            "latitude": lat,
            "longitude": lon,
        }, timeout=15))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for resp in results:
        if not isinstance(resp, Exception) and resp.status_code == 201:
            customers.append(resp.json()["id"])

    print(
        f"  ✓ {len(restaurants)} restaurantes | "
        f"{len(customers)} clientes | "
        f"{len(couriers)} entregadores"
    )
    return restaurants, customers, couriers


# ============================================================
# EXECUÇÃO DO CENÁRIO DE CARGA
# ============================================================
async def run_load_scenario(
    base: str,
    config: dict,
    restaurants: List[str],
    customers: List[str],
) -> dict:
    target_ops = config["target_ops"]
    duration = config["duration_seconds"]

    registration_latencies: List[float] = []
    query_latencies: List[float] = []
    errors = 0
    orders_created = 0

    # Semáforos para controlar concorrência
    # Mais slots para taxas mais altas
    max_create_concurrent = max(30, target_ops)
    max_advance_concurrent = max(50, target_ops // 2)
    create_sem = asyncio.Semaphore(max_create_concurrent)
    advance_sem = asyncio.Semaphore(max_advance_concurrent)

    limits = httpx.Limits(
        max_connections=max_create_concurrent * 3,
        max_keepalive_connections=max_create_concurrent * 2,
    )

    print(f"\n→ Iniciando carga: {target_ops} ped/s por {duration}s...")
    print(f"  {'Tempo':>8} {'Criados':>8} {'Erros':>6} {'P95 Reg':>10} {'P95 Query':>11}")
    print("  " + "─" * 50)

    start_time = time.monotonic()
    interval = 1.0 / target_ops
    last_report = start_time
    next_fire = start_time

    # Rastreia todas as tasks em flight
    all_tasks: set = set()

    async with httpx.AsyncClient(
        limits=limits,
        timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
    ) as client:

        async def advance_order(order_id: str) -> None:
            async with advance_sem:
                for status in STATUS_CHAIN:
                    try:
                        await client.patch(
                            f"{base}/api/orders/{order_id}/status",
                            json={"status": status},
                            timeout=10.0,
                        )
                    except Exception:
                        break

        async def create_order() -> None:
            nonlocal orders_created, errors
            async with create_sem:
                customer_id = random.choice(customers)
                restaurant_id = random.choice(restaurants)
                t0 = time.monotonic()
                try:
                    resp = await client.post(f"{base}/api/orders", json={
                        "customer_id": customer_id,
                        "restaurant_id": restaurant_id,
                        "items": [{
                            "name": fake.word().capitalize(),
                            "qty": random.randint(1, 2),
                            "price": round(random.uniform(15, 60), 2),
                        }],
                    })
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    if resp.status_code == 201:
                        registration_latencies.append(elapsed_ms)
                        orders_created += 1
                        order_id = resp.json()["order_id"]
                        # Avança status em background para liberar entregador
                        adv = asyncio.create_task(advance_order(order_id))
                        all_tasks.add(adv)
                        adv.add_done_callback(all_tasks.discard)
                    else:
                        errors += 1
                except Exception:
                    errors += 1

        async def query_orders() -> None:
            t0 = time.monotonic()
            try:
                resp = await client.get(f"{base}/api/orders?limit=20")
                elapsed_ms = (time.monotonic() - t0) * 1000
                if resp.status_code == 200:
                    query_latencies.append(elapsed_ms)
            except Exception:
                pass

        query_counter = 0

        while True:
            now = time.monotonic()
            elapsed = now - start_time
            if elapsed >= duration:
                break

            # Rate control: espera até o próximo slot de disparo
            sleep_for = next_fire - now
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)

            next_fire += interval

            # Criar pedido
            ct = asyncio.create_task(create_order())
            all_tasks.add(ct)
            ct.add_done_callback(all_tasks.discard)

            # Intercala consulta a cada 10 criações
            query_counter += 1
            if query_counter % 10 == 0:
                qt = asyncio.create_task(query_orders())
                all_tasks.add(qt)
                qt.add_done_callback(all_tasks.discard)

            # Progress a cada 10s
            now = time.monotonic()
            if now - last_report >= 10:
                elapsed_rep = now - start_time
                p95_reg = percentile(registration_latencies, 95) if registration_latencies else 0
                p95_qry = percentile(query_latencies, 95) if query_latencies else 0
                print(
                    f"  {elapsed_rep:>7.1f}s "
                    f"{orders_created:>8d} "
                    f"{errors:>6d} "
                    f"{p95_reg:>9.1f}ms "
                    f"{p95_qry:>10.1f}ms"
                )
                last_report = now

        # Aguarda todas as tasks (criação + avanço de status)
        if all_tasks:
            await asyncio.gather(*all_tasks, return_exceptions=True)

    total_elapsed = time.monotonic() - start_time
    actual_ops = orders_created / total_elapsed if total_elapsed > 0 else 0

    # Métricas de latência
    def metrics(data: List[float]) -> dict:
        if not data:
            return {"p50": 0, "p95": 0, "p99": 0, "mean": 0, "samples": 0}
        return {
            "p50": round(percentile(data, 50), 2),
            "p95": round(percentile(data, 95), 2),
            "p99": round(percentile(data, 99), 2),
            "mean": round(statistics.mean(data), 2),
            "samples": len(data),
        }

    reg = metrics(registration_latencies)
    qry = metrics(query_latencies)
    sla_ok = reg["p95"] < SLA_MS and qry["p95"] < SLA_MS

    return {
        "scenario_ops": target_ops,
        "description": config["description"],
        "duration_seconds": round(total_elapsed, 2),
        "total_orders": orders_created + errors,
        "success_orders": orders_created,
        "error_orders": errors,
        "actual_ops": round(actual_ops, 2),
        "registration": reg,
        "query": qry,
        "sla_compliant": sla_ok,
        "sla_threshold_ms": SLA_MS,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ============================================================
# OUTPUT
# ============================================================
def print_result(result: dict) -> None:
    sla_icon = "✅" if result["sla_compliant"] else "❌"
    reg = result["registration"]
    qry = result["query"]

    def sla_tag(ms: float) -> str:
        return "✅ < 500ms" if ms < SLA_MS else "❌ > 500ms (SLA VIOLADO)"

    print(f"\n{'═'*60}")
    print(f"  {sla_icon} {result['description']}")
    print(f"{'═'*60}")
    print(f"  Taxa alvo:      {result['scenario_ops']} ped/s")
    print(f"  Taxa medida:    {result['actual_ops']:.1f} ped/s")
    print(f"  Pedidos OK:     {result['success_orders']} / {result['total_orders']}")
    print(f"  Erros:          {result['error_orders']}")
    print()
    print(f"  REGISTRO (POST /api/orders)  [{reg['samples']} amostras]")
    print(f"    P50:  {reg['p50']:>8.1f} ms")
    print(f"    P95:  {reg['p95']:>8.1f} ms  ← {sla_tag(reg['p95'])}")
    print(f"    P99:  {reg['p99']:>8.1f} ms")
    print(f"    Média:{reg['mean']:>8.1f} ms")
    print()
    print(f"  CONSULTA (GET /api/orders)  [{qry['samples']} amostras]")
    print(f"    P50:  {qry['p50']:>8.1f} ms")
    print(f"    P95:  {qry['p95']:>8.1f} ms  ← {sla_tag(qry['p95'])}")
    print(f"    P99:  {qry['p99']:>8.1f} ms")
    print(f"    Média:{qry['mean']:>8.1f} ms")
    print()
    print(f"  SLA GERAL (P95 < 500ms): {sla_icon} {'APROVADO' if result['sla_compliant'] else 'REPROVADO'}")


def save_result(result: dict) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"load_test_{result['scenario_ops']}ops_{ts}.json"
    with open(fname, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    return fname


async def push_to_dashboard(result: dict, dashboard_url: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{dashboard_url}/api/dashboard/load-test-results",
                json=result,
            )
            if resp.status_code in (200, 201):
                print(f"  → Resultado enviado ao dashboard: {dashboard_url}")
            else:
                print(f"  ⚠ Dashboard retornou {resp.status_code}")
    except Exception as e:
        print(f"  ⚠ Falha ao enviar para dashboard: {e}")


# ============================================================
# ENTRYPOINTS
# ============================================================
async def run_single(base: str, target_ops: int, dashboard_url: Optional[str]) -> dict:
    config = SCENARIOS[target_ops]
    print(f"DijkFood — Simulador de Carga")
    print(f"Cenário: {config['description']}")
    print(f"Proporção entregadores/clientes: 3:1 ({config['couriers']}/{config['customers']})")
    print(f"SLA: P95 < {SLA_MS:.0f}ms para registro e consulta\n")

    seed_limits = httpx.Limits(max_connections=60, max_keepalive_connections=40)
    async with httpx.AsyncClient(limits=seed_limits, timeout=httpx.Timeout(30.0)) as client:
        restaurants, customers, couriers = await seed_entities(client, base, config)

    if not restaurants or not customers or not couriers:
        print("ERRO: entidades insuficientes para o teste. Encerrando.")
        sys.exit(1)

    result = await run_load_scenario(base, config, restaurants, customers)
    print_result(result)

    fname = save_result(result)
    print(f"\n  → Resultado salvo: {fname}")

    if dashboard_url:
        await push_to_dashboard(result, dashboard_url)

    return result


async def run_all(base: str, dashboard_url: Optional[str]) -> None:
    print("DijkFood — Simulador de Carga (todos os cenários)")
    print(f"ALB: {base}")
    print(f"Proporção: 3 entregadores por cliente  |  SLA: P95 < {SLA_MS:.0f}ms\n")

    all_results = []

    for i, target_ops in enumerate([10, 100, 200]):
        config = SCENARIOS[target_ops]
        print(f"\n{'#'*60}")
        print(f"# CENÁRIO {i+1}/3: {config['description']}")
        print(f"{'#'*60}")

        seed_limits = httpx.Limits(max_connections=80, max_keepalive_connections=60)
        async with httpx.AsyncClient(limits=seed_limits, timeout=httpx.Timeout(30.0)) as client:
            restaurants, customers, couriers = await seed_entities(client, base, config)

        if not restaurants or not customers or not couriers:
            print(f"ERRO: entidades insuficientes para {target_ops} OPS. Pulando.")
            continue

        result = await run_load_scenario(base, config, restaurants, customers)
        print_result(result)
        all_results.append(result)

        fname = save_result(result)
        print(f"\n  → Resultado salvo: {fname}")

        if dashboard_url:
            await push_to_dashboard(result, dashboard_url)

        if target_ops != 200:
            print(f"\n  Aguardando 15s antes do próximo cenário...")
            await asyncio.sleep(15)

    # Resumo comparativo
    if len(all_results) > 1:
        print(f"\n{'═'*72}")
        print("  RESUMO COMPARATIVO")
        print(f"{'═'*72}")
        print(f"  {'Cenário':26} {'Ped/s':>7} {'P95 Reg':>10} {'P95 Query':>11} {'SLA':>5}")
        print(f"  {'─'*65}")
        for r in all_results:
            sla = "✅" if r["sla_compliant"] else "❌"
            print(
                f"  {r['description']:26} "
                f"{r['actual_ops']:>6.1f} "
                f"{r['registration']['p95']:>9.1f}ms "
                f"{r['query']['p95']:>10.1f}ms "
                f"{sla:>5}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="DijkFood — Simulador de Carga (10/100/200 ped/s, proporção 3:1)",
    )
    parser.add_argument(
        "--alb", required=True,
        help="URL base do ALB, ex: http://dijkfood-alb-....elb.amazonaws.com",
    )
    parser.add_argument(
        "--scenario", choices=["10", "100", "200", "all"], default="all",
        help="Cenário a executar (default: all)",
    )
    parser.add_argument(
        "--dashboard", default=None,
        help="URL do dashboard-analytics para enviar resultados (opcional)",
    )
    args = parser.parse_args()

    base = args.alb.rstrip("/")
    dashboard_url = args.dashboard.rstrip("/") if args.dashboard else None

    try:
        if args.scenario == "all":
            asyncio.run(run_all(base, dashboard_url))
        else:
            asyncio.run(run_single(base, int(args.scenario), dashboard_url))
    except KeyboardInterrupt:
        print("\n\nSimulação interrompida pelo usuário.")


if __name__ == "__main__":
    main()
