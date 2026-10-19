from __future__ import annotations

from collections import Counter

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from optimizer.batching import OrderSpec, WorkerSpec, build_trips, cpsat_batches, plan_batches
from optimizer.benchmark import run
from optimizer.layout import WarehouseLayout, dijkstra
from optimizer.routing import Algorithm, PickItem, lower_bound, route_trip, solve_route, validate_on_graph

pytestmark = pytest.mark.solver

LAYOUT = WarehouseLayout.from_preset("small")


def _item(aisle: int, bay: int, order: str = "ORD-00000001", line: int = 1, vol: float = 1.0) -> PickItem:
    x, y = LAYOUT.pick_point(aisle, bay)
    return PickItem(f"LOC-A{aisle:02d}-{bay:02d}-1", "SKU-000001", 1, x, y, order, line, vol)


picks = st.lists(
    st.tuples(st.integers(1, LAYOUT.aisle_count), st.integers(1, LAYOUT.bays)), min_size=1, max_size=25
)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(picks, st.sampled_from([a.value for a in Algorithm]))
def test_route_visits_every_item_exactly_once_on_graph(raw: list[tuple[int, int]], algo: str) -> None:
    items = [_item(a, b, line=i) for i, (a, b) in enumerate(raw)]
    trip = route_trip(LAYOUT, items, algo, time_limit_s=0.2)
    assert Counter(i.line_id for i in trip.items) == Counter(i.line_id for i in items)
    assert trip.waypoints[0] == LAYOUT.depot and trip.waypoints[-1] == LAYOUT.depot
    assert validate_on_graph(LAYOUT, trip.waypoints)
    # łamana przechodzi przez każdy punkt pobrania
    wp = set(trip.waypoints)
    assert all(i.point in wp for i in items)
    assert trip.distance_m + 1e-6 >= lower_bound(LAYOUT, [i.point for i in items])


@settings(max_examples=40, deadline=None)
@given(picks)
def test_ortools_never_worse_than_nearest_neighbor(raw: list[tuple[int, int]]) -> None:
    items = [_item(a, b, line=i) for i, (a, b) in enumerate(raw)]
    nn = route_trip(LAYOUT, items, Algorithm.NEAREST_NEIGHBOR)
    ort = route_trip(LAYOUT, items, Algorithm.ORTOOLS, time_limit_s=0.2)
    assert ort.distance_m <= nn.distance_m + 1e-6


@settings(max_examples=40, deadline=None)
@given(st.tuples(st.integers(1, 6), st.integers(1, 20)), st.tuples(st.integers(1, 6), st.integers(1, 20)))
def test_closed_form_distance_equals_dijkstra(a: tuple[int, int], b: tuple[int, int]) -> None:
    p, q = LAYOUT.pick_point(*a), LAYOUT.pick_point(*b)
    adj = LAYOUT.graph_edges([p, q])
    assert abs(dijkstra(adj, p, q) - LAYOUT.distance(p, q)) < 1e-6


def test_single_aisle_return_route() -> None:
    items = [_item(2, 4), _item(2, 10, line=2)]
    for algo in Algorithm:
        trip = route_trip(LAYOUT, items, algo)
        x = LAYOUT.aisle_x(2)
        far = LAYOUT.bay_y(10)
        assert trip.distance_m == pytest.approx(2 * (x + far), abs=1e-6), algo


def test_s_shape_traverses_full_aisles() -> None:
    items = [_item(1, 1), _item(3, 1, line=2)]
    trip = route_trip(LAYOUT, items, Algorithm.S_SHAPE)
    # dwie alejki: pełne przejście alejki 1 i alejki 3 + odcinki poprzeczne
    expected = 2 * LAYOUT.aisle_x(3) + 2 * (LAYOUT.back_y - LAYOUT.front_y)
    assert trip.distance_m == pytest.approx(expected)


def test_multi_trip_solution_and_stops() -> None:
    trips = [[_item(1, 3), _item(2, 5, line=2)], [_item(5, 7, line=3)]]
    sol = solve_route(LAYOUT, trips, Algorithm.ORTOOLS, time_limit_s=0.3)
    stops = sol.stops(LAYOUT.depot)
    assert [s["type"] for s in stops].count("depot") == 2
    assert sol.pick_count == 3
    assert sol.duration_s(1.0, 10.0) > sol.distance_m


def test_batching_respects_capacity_and_max_orders() -> None:
    orders = [
        OrderSpec(f"ORD-{i:08d}", (_item(1 + i % 6, 1 + i % 20, f"ORD-{i:08d}", i, vol=30.0),), due_rank=i)
        for i in range(30)
    ]
    trips, too_big = build_trips(orders, capacity_l=100.0, max_orders=4)
    assert not too_big
    assert sorted(o.order_id for t in trips for o in t) == sorted(o.order_id for o in orders)
    for t in trips:
        assert len(t) <= 4
        assert sum(o.volume_l for o in t) <= 100.0


def test_plan_batches_time_feasibility() -> None:
    orders = [
        OrderSpec(f"ORD-{i:08d}", (_item(1 + i % 6, 1 + i % 20, f"ORD-{i:08d}", i),), i) for i in range(20)
    ]
    w = [WorkerSpec(f"WRK-{i:03d}", 1.1, 12, 1.0, 400, 10, available_s=3600) for i in (1, 2)]
    plan = plan_batches(LAYOUT, orders, w)
    assert plan.feasible
    assert sorted(o for p in plan.workers for o in p.order_ids) == sorted(o.order_id for o in orders)
    tight = [WorkerSpec("WRK-001", 1.1, 12, 1.0, 400, 10, available_s=60)]
    assert not plan_batches(LAYOUT, orders, tight).feasible


def test_cpsat_batches_not_worse_than_heuristic_in_aisles() -> None:
    orders = [
        OrderSpec(f"ORD-{i:08d}", (_item(1 + (i * 7) % 6, 1 + i % 20, f"ORD-{i:08d}", i, 10.0),), i)
        for i in range(12)
    ]
    cp = cpsat_batches(orders, n_batches=3, capacity_l=40.0, max_orders=4, time_limit_s=2.0)
    assert cp is not None
    spec = {o.order_id: o for o in orders}
    cp_aisles = sum(len({a for oid in t for a in spec[oid].aisles}) for t in cp)
    heur, _ = build_trips(orders, 40.0, 4)
    heur_aisles = sum(len({a for o in t for a in o.aisles}) for t in heur)
    assert cp_aisles <= heur_aisles


def test_benchmark_ortools_beats_s_shape() -> None:
    rows = {r.algorithm: r for r in run(seed=42, n_orders=15, instances=5, size="medium", time_limit_s=0.3)}
    assert rows["ortools"].total_m < rows["s_shape"].total_m
    assert rows["ortools"].gain_vs_s_shape_pct > 5
