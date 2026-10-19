"""Podział zamówień fali na pracowników i kursy (trips) z ograniczeniem pojemności wózka.

Heurystyka „seed”: kurs zaczyna zamówienie o najwcześniejszym terminie; dobieramy zamówienia, które
dokładają najmniej nowych alejek (podobieństwo lokalizacji), aż do limitu objętości / liczby zamówień.
Kursy przydzielamy pracownikom metodą LPT (najdłuższy kurs -> najmniej obciążony pracownik, z uwzględnieniem
prędkości). Opcjonalnie CP-SAT minimalizuje łączną liczbę odwiedzanych alejek (proxy dystansu).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from optimizer.layout import WarehouseLayout
from optimizer.routing import Algorithm, PickItem, route_trip


@dataclass(frozen=True)
class OrderSpec:
    order_id: str
    items: tuple[PickItem, ...]
    due_rank: float = 0.0  # mniejsze = pilniejsze (np. sekundy do terminu)
    priority: str = "standard"

    @property
    def volume_l(self) -> float:
        return sum(i.volume_l * i.qty for i in self.items)

    @property
    def aisles(self) -> frozenset[float]:
        return frozenset(i.x for i in self.items)


@dataclass(frozen=True)
class WorkerSpec:
    worker_id: str
    speed_mps: float
    pick_time_s: float
    efficiency: float
    cart_capacity_l: float
    cart_max_orders: int
    available_s: float  # czas do cut-off (lub końca zmiany)


@dataclass
class WorkerPlan:
    worker_id: str
    trips: list[list[str]] = field(default_factory=list)
    est_distance_m: float = 0.0
    est_duration_s: float = 0.0
    available_s: float = 0.0

    @property
    def order_ids(self) -> list[str]:
        return [o for t in self.trips for o in t]


@dataclass
class BatchPlan:
    workers: list[WorkerPlan]
    unassigned: list[str]
    problems: list[dict]

    @property
    def feasible(self) -> bool:
        return not self.problems and not self.unassigned

    @property
    def est_distance_m(self) -> float:
        return sum(w.est_distance_m for w in self.workers)

    @property
    def makespan_s(self) -> float:
        return max((w.est_duration_s for w in self.workers), default=0.0)


def _trip_estimate(layout: WarehouseLayout, orders: list[OrderSpec], w: WorkerSpec) -> tuple[float, float]:
    items = [i for o in orders for i in o.items]
    trip = route_trip(layout, items, Algorithm.NEAREST_NEIGHBOR)
    speed = w.speed_mps * w.efficiency
    picks = sum(w.pick_time_s / w.efficiency * (1 + 0.1 * (i.qty - 1)) for i in items)
    return trip.distance_m, trip.distance_m / max(speed, 0.1) + picks


def build_trips(
    orders: list[OrderSpec], capacity_l: float, max_orders: int
) -> tuple[list[list[OrderSpec]], list[str]]:
    pending = sorted(orders, key=lambda o: (o.due_rank, o.order_id))
    trips: list[list[OrderSpec]] = []
    too_big: list[str] = []
    while pending:
        seed = pending.pop(0)
        if seed.volume_l > capacity_l:
            too_big.append(seed.order_id)
            continue
        trip = [seed]
        vol = seed.volume_l
        aisles = set(seed.aisles)
        while len(trip) < max_orders:
            best = None
            best_key: tuple[float, float, str] | None = None
            for o in pending:
                if vol + o.volume_l > capacity_l:
                    continue
                new_aisles = len(o.aisles - aisles)
                key = (new_aisles, o.due_rank, o.order_id)
                if best_key is None or key < best_key:
                    best, best_key = o, key
            if best is None:
                break
            pending.remove(best)
            trip.append(best)
            vol += best.volume_l
            aisles |= best.aisles
        trips.append(trip)
    return trips, too_big


def plan_batches(layout: WarehouseLayout, orders: list[OrderSpec], workers: list[WorkerSpec]) -> BatchPlan:
    if not workers:
        return BatchPlan([], [o.order_id for o in orders], [{"reason": "no_workers"}])
    cap = min(w.cart_capacity_l for w in workers)
    max_orders = min(w.cart_max_orders for w in workers)
    trips, too_big = build_trips(orders, cap, max_orders)
    plans = {w.worker_id: WorkerPlan(w.worker_id, available_s=w.available_s) for w in workers}
    spec = {w.worker_id: w for w in workers}
    estimates = []
    for trip in trips:
        # oszacowanie dla „przeciętnego” pracownika, do sortowania LPT
        d, t = _trip_estimate(layout, trip, workers[0])
        estimates.append((t, d, trip))
    estimates.sort(key=lambda e: (-e[0], e[2][0].order_id))
    for _t, _d, trip in estimates:
        best_id = min(
            plans,
            key=lambda wid: (plans[wid].est_duration_s + _trip_estimate(layout, trip, spec[wid])[1], wid),
        )
        d, t = _trip_estimate(layout, trip, spec[best_id])
        plans[best_id].trips.append([o.order_id for o in trip])
        plans[best_id].est_distance_m += d
        plans[best_id].est_duration_s += t
    problems = []
    for p in plans.values():
        if p.est_duration_s > p.available_s:
            problems.append(
                {
                    "reason": "time_exceeded",
                    "worker_id": p.worker_id,
                    "est_duration_s": round(p.est_duration_s),
                    "available_s": round(p.available_s),
                }
            )
    if too_big:
        problems.append({"reason": "order_exceeds_cart_capacity", "order_ids": too_big})
    return BatchPlan([p for p in plans.values() if p.trips], too_big, problems)


def cpsat_batches(
    orders: list[OrderSpec], n_batches: int, capacity_l: float, max_orders: int, time_limit_s: float = 2.0
) -> list[list[str]] | None:
    """CP-SAT: przydział zamówień do kursów minimalizujący liczbę par (kurs, alejka). None = brak rozwiązania."""
    from ortools.sat.python import cp_model

    if not orders:
        return []
    aisles = sorted({a for o in orders for a in o.aisles})
    m = cp_model.CpModel()
    x = {(i, b): m.new_bool_var(f"x{i}_{b}") for i in range(len(orders)) for b in range(n_batches)}
    y = {(a, b): m.new_bool_var(f"y{a}_{b}") for a in range(len(aisles)) for b in range(n_batches)}
    for i in range(len(orders)):
        m.add_exactly_one(x[i, b] for b in range(n_batches))
    for b in range(n_batches):
        m.add(
            sum(int(round(orders[i].volume_l * 100)) * x[i, b] for i in range(len(orders)))
            <= int(capacity_l * 100)
        )
        m.add(sum(x[i, b] for i in range(len(orders))) <= max_orders)
        for i, o in enumerate(orders):
            for a_idx, a in enumerate(aisles):
                if a in o.aisles:
                    m.add_implication(x[i, b], y[a_idx, b])
    # symetria: zamówienie 0 w kursie 0
    m.add(x[0, 0] == 1)
    m.minimize(sum(y.values()))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = 42
    status = solver.solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    out: list[list[str]] = [[] for _ in range(n_batches)]
    for (i, b), var in x.items():
        if solver.value(var):
            out[b].append(orders[i].order_id)
    return [t for t in out if t]
