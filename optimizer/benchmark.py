"""Benchmark algorytmów tras: OR-Tools vs S-shape vs largest gap vs nearest neighbour.

Użycie:
    python -m optimizer.benchmark --seed 42 --orders 40 --instances 20 [--size medium] [--out raport.md]

Instancje: partie zamówień (1–4 linie) losowane z rozkładem Pareto po lokalizacjach (popularne SKU częściej),
na układzie `medium` (20 alejek × 50 przęseł). Każdy algorytm dostaje te same punkty (jeden kurs).
"""

from __future__ import annotations

import argparse
import statistics
import time
from dataclasses import dataclass

import numpy as np

from optimizer.layout import WarehouseLayout
from optimizer.routing import Algorithm, PickItem, gain_pct, route_trip, validate_on_graph

ALGOS = [Algorithm.S_SHAPE, Algorithm.LARGEST_GAP, Algorithm.NEAREST_NEIGHBOR, Algorithm.ORTOOLS]


@dataclass
class BenchRow:
    algorithm: str
    mean_m: float
    median_m: float
    total_m: float
    gain_vs_s_shape_pct: float
    wins_vs_s_shape: int
    mean_runtime_ms: float


def make_instance(layout: WarehouseLayout, rng: np.random.Generator, n_orders: int) -> list[PickItem]:
    aisles = layout.aisle_count
    bays = layout.bays
    n_slots = aisles * bays
    pop = 1.0 / np.arange(1, n_slots + 1) ** 0.9
    perm = rng.permutation(n_slots)
    p = pop[perm] / pop.sum()
    items = []
    for o in range(n_orders):
        for _ in range(int(rng.integers(1, 5))):
            slot = int(rng.choice(n_slots, p=p))
            aisle, bay = slot // bays + 1, slot % bays + 1
            x, y = layout.pick_point(aisle, bay)
            items.append(PickItem(f"LOC-A{aisle:02d}-{bay:02d}-1", "SKU-000001", 1, x, y, f"ORD-{o:08d}"))
    return items


def run(
    seed: int, n_orders: int, instances: int, size: str = "medium", time_limit_s: float = 1.0
) -> list[BenchRow]:
    layout = WarehouseLayout.from_preset(size)
    rng = np.random.default_rng(seed)
    dist: dict[str, list[float]] = {a.value: [] for a in ALGOS}
    rt: dict[str, list[float]] = {a.value: [] for a in ALGOS}
    for _ in range(instances):
        items = make_instance(layout, rng, n_orders)
        for algo in ALGOS:
            t0 = time.perf_counter()
            trip = route_trip(layout, items, algo, time_limit_s=time_limit_s)
            rt[algo.value].append((time.perf_counter() - t0) * 1000)
            assert validate_on_graph(layout, trip.waypoints), algo
            assert len(trip.items) == len(items)
            dist[algo.value].append(trip.distance_m)
    base = dist[Algorithm.S_SHAPE.value]
    rows = []
    for algo in ALGOS:
        d = dist[algo.value]
        rows.append(
            BenchRow(
                algorithm=algo.value,
                mean_m=statistics.fmean(d),
                median_m=statistics.median(d),
                total_m=sum(d),
                gain_vs_s_shape_pct=gain_pct(sum(base), sum(d)),
                wins_vs_s_shape=sum(1 for a, b in zip(d, base, strict=True) if a < b - 1e-9),
                mean_runtime_ms=statistics.fmean(rt[algo.value]),
            )
        )
    return rows


def to_markdown(rows: list[BenchRow], *, seed: int, n_orders: int, instances: int, size: str) -> str:
    lines = [
        f"### Benchmark tras (seed={seed}, układ {size}, {instances} instancji × {n_orders} zamówień)",
        "",
        "| Algorytm | Śr. dystans [m] | Mediana [m] | Zysk vs S-shape | Instancje lepsze od S-shape | Śr. czas obliczeń [ms] |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r.algorithm} | {r.mean_m:.1f} | {r.median_m:.1f} | {r.gain_vs_s_shape_pct:+.1f}% | "
            f"{r.wins_vs_s_shape}/{instances} | {r.mean_runtime_ms:.0f} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Benchmark algorytmów tras kompletacji")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--orders", type=int, default=20)
    ap.add_argument("--instances", type=int, default=20)
    ap.add_argument("--size", default="medium", choices=["small", "medium", "large"])
    ap.add_argument("--time-limit", type=float, default=1.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    rows = run(args.seed, args.orders, args.instances, args.size, args.time_limit)
    md = to_markdown(rows, seed=args.seed, n_orders=args.orders, instances=args.instances, size=args.size)
    print(md)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(md)


if __name__ == "__main__":
    main()
