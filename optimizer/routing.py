"""Trasy kompletacji: OR-Tools (TSP), S-shape, largest gap, nearest neighbour.

Wejście: lista pozycji do pobrania (PickItem) podzielona na kursy (trips) — każdy kurs startuje i kończy się
w punkcie pakowania (depot). Podział na kursy wynika z pojemności wózka (optimizer.batching) i jest wspólny
dla wszystkich algorytmów, więc porównanie dotyczy wyłącznie kolejności odwiedzin.

Każdy algorytm zwraca jawną łamaną (waypoints) poruszającą się wyłącznie po grafie alejek: odcinki pionowe
w osiach alejek i poziome w alejkach poprzecznych. Długość trasy = długość łamanej.
- S-shape i largest gap: łamana budowana zgodnie z definicją heurystyki (pełne przejścia alejek / wejścia do
  największej luki), a nie „najkrótsze połączenia” — to uczciwy punkt odniesienia.
- NN i OR-Tools: kolejność odwiedzin połączona najkrótszymi ścieżkami w grafie.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum

from optimizer.layout import Point, WarehouseLayout

EPS = 1e-9


class Algorithm(StrEnum):
    ORTOOLS = "ortools"
    S_SHAPE = "s_shape"
    LARGEST_GAP = "largest_gap"
    NEAREST_NEIGHBOR = "nearest_neighbor"


@dataclass(frozen=True)
class PickItem:
    location_id: str
    sku_id: str
    qty: int
    x: float
    y: float
    order_id: str = ""
    line_id: int = 0
    volume_l: float = 0.0

    @property
    def point(self) -> Point:
        return (self.x, self.y)


@dataclass
class TripRoute:
    items: list[PickItem]  # w kolejności pobrania
    waypoints: list[Point]
    distance_m: float


@dataclass
class RouteSolution:
    algorithm: str
    trips: list[TripRoute] = field(default_factory=list)

    @property
    def distance_m(self) -> float:
        return sum(t.distance_m for t in self.trips)

    @property
    def pick_count(self) -> int:
        return sum(len(t.items) for t in self.trips)

    def path(self) -> list[list[float]]:
        out: list[list[float]] = []
        for t in self.trips:
            for p in t.waypoints:
                q = [round(p[0], 3), round(p[1], 3)]
                if not out or out[-1] != q:
                    out.append(q)
        return out

    def stops(self, depot: Point) -> list[dict]:
        stops: list[dict] = []
        for trip_no, t in enumerate(self.trips, start=1):
            for it in t.items:
                stops.append(
                    {
                        "type": "pick",
                        "trip": trip_no,
                        "x": it.x,
                        "y": it.y,
                        "location_id": it.location_id,
                        "sku_id": it.sku_id,
                        "qty": it.qty,
                        "order_id": it.order_id,
                        "line_id": it.line_id,
                    }
                )
            stops.append({"type": "depot", "trip": trip_no, "x": depot[0], "y": depot[1]})
        return stops

    def duration_s(self, speed_mps: float, pick_time_s: float) -> float:
        walk = self.distance_m / max(speed_mps, 0.1)
        picks = sum(pick_time_s * (1 + 0.1 * (it.qty - 1)) for t in self.trips for it in t.items)
        return walk + picks


# --------------------------------------------------------------------------------------------- pomocnicze
def polyline_length(points: list[Point]) -> float:
    return sum(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(points, points[1:], strict=False))


def _group_by_point(items: list[PickItem]) -> dict[Point, list[PickItem]]:
    groups: dict[Point, list[PickItem]] = defaultdict(list)
    for it in items:
        groups[it.point].append(it)
    return groups


def _sorted_group_items(group: list[PickItem]) -> list[PickItem]:
    return sorted(group, key=lambda i: (i.location_id, i.order_id, i.line_id))


def _connect(layout: WarehouseLayout, seq: list[Point]) -> list[Point]:
    """Łączy punkty najkrótszymi ścieżkami w grafie alejek (depot -> ... -> depot)."""
    pts = [layout.depot, *seq, layout.depot]
    out: list[Point] = [layout.depot]
    for a, b in zip(pts, pts[1:], strict=False):
        out.extend(layout.path(a, b)[1:])
    return out


def _items_in_visit_order(groups: dict[Point, list[PickItem]], seq: list[Point]) -> list[PickItem]:
    seen: set[Point] = set()
    out: list[PickItem] = []
    for p in seq:
        if p in seen:
            continue
        seen.add(p)
        out.extend(_sorted_group_items(groups[p]))
    return out


def _aisle_points(layout: WarehouseLayout, points: list[Point]) -> dict[float, list[float]]:
    by_x: dict[float, list[float]] = defaultdict(list)
    for x, y in points:
        by_x[x].append(y)
    return {x: sorted(set(ys)) for x, ys in sorted(by_x.items())}


# --------------------------------------------------------------------------------------------- heurystyki
def s_shape_sequence(layout: WarehouseLayout, points: list[Point]) -> tuple[list[Point], list[Point]]:
    """S-shape (traversal): każda alejka z pobraniami przechodzona w całości; przy nieparzystej liczbie
    alejek ostatnia obsługiwana „nawrotem” (wejście od przodu do najdalszego pobrania i powrót)."""
    f, b = layout.front_y, layout.back_y
    aisles = _aisle_points(layout, points)
    xs = list(aisles)
    wp: list[Point] = [layout.depot]
    seq: list[Point] = []
    at_front = True
    m = len(xs)
    for idx, x in enumerate(xs):
        ys = aisles[x]
        last_odd = idx == m - 1 and m % 2 == 1
        y_now = f if at_front else b
        wp.append((x, y_now))
        if last_odd and at_front:
            for y in ys:
                wp.append((x, y))
                seq.append((x, y))
            wp.append((x, f))
            continue
        ordered = ys if at_front else list(reversed(ys))
        for y in ordered:
            wp.append((x, y))
            seq.append((x, y))
        at_front = not at_front
        wp.append((x, f if at_front else b))
    if wp[-1][1] != f:  # zakończenie z tyłu: powrót do przodu ostatnią alejką
        wp.append((wp[-1][0], f))
    wp.append(layout.depot)
    return seq, wp


def largest_gap_sequence(layout: WarehouseLayout, points: list[Point]) -> tuple[list[Point], list[Point]]:
    """Largest gap (Ratliff & Rosenthal / Hall): skrajne alejki przechodzone w całości, w alejkach pośrednich
    pomijana jest największa luka — część pobrań od tyłu (w drodze tam), część od przodu (w drodze powrotnej)."""
    f, b = layout.front_y, layout.back_y
    aisles = _aisle_points(layout, points)
    xs = list(aisles)
    wp: list[Point] = [layout.depot]
    seq: list[Point] = []
    if not xs:
        return seq, [layout.depot, layout.depot]
    if len(xs) == 1:
        x = xs[0]
        ys = aisles[x]
        wp.append((x, f))
        for y in ys:
            wp.append((x, y))
            seq.append((x, y))
        wp += [(x, f), layout.depot]
        return seq, wp

    split: dict[float, tuple[list[float], list[float]]] = {}
    for x in xs[1:-1]:
        ys = aisles[x]
        bounds = [f, *ys, b]
        gaps = [(bounds[i + 1] - bounds[i], i) for i in range(len(bounds) - 1)]
        _, gi = max(gaps, key=lambda g: (g[0], -g[1]))
        # luka gi leży między bounds[gi] a bounds[gi+1]; pobrania o indeksach < gi -> od przodu
        from_front = ys[:gi]
        from_back = ys[gi:]
        split[x] = (from_front, from_back)

    first, last = xs[0], xs[-1]
    # 1) pierwsza alejka w całości (przód -> tył)
    wp.append((first, f))
    for y in aisles[first]:
        wp.append((first, y))
        seq.append((first, y))
    wp.append((first, b))
    # 2) alejki pośrednie od tyłu (w prawo)
    for x in xs[1:-1]:
        _, from_back = split[x]
        if from_back:
            wp.append((x, b))
            for y in reversed(from_back):
                wp.append((x, y))
                seq.append((x, y))
            wp.append((x, b))
    # 3) ostatnia alejka w całości (tył -> przód)
    wp.append((last, b))
    for y in reversed(aisles[last]):
        wp.append((last, y))
        seq.append((last, y))
    wp.append((last, f))
    # 4) alejki pośrednie od przodu (w lewo)
    for x in reversed(xs[1:-1]):
        from_front, _ = split[x]
        if from_front:
            wp.append((x, f))
            for y in from_front:
                wp.append((x, y))
                seq.append((x, y))
            wp.append((x, f))
    wp.append(layout.depot)
    return seq, wp


def nearest_neighbor_sequence(layout: WarehouseLayout, points: list[Point]) -> list[Point]:
    remaining = sorted(set(points))
    cur = layout.depot
    seq: list[Point] = []
    while remaining:
        nxt = min(remaining, key=lambda p: (layout.distance(cur, p), p))
        seq.append(nxt)
        remaining.remove(nxt)
        cur = nxt
    return seq


def ortools_sequence(layout: WarehouseLayout, points: list[Point], time_limit_s: float = 1.0) -> list[Point]:
    """TSP w OR-Tools na macierzy odległości grafu alejek. Rozwiązanie startowe = nearest neighbour,
    więc wynik nigdy nie jest gorszy od NN; dalej Guided Local Search do limitu czasu."""
    uniq = sorted(set(points))
    if len(uniq) <= 2:
        return nearest_neighbor_sequence(layout, uniq)
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    nodes = [layout.depot, *uniq]
    n = len(nodes)
    matrix = [[int(round(layout.distance(a, b) * 100)) for b in nodes] for a in nodes]  # cm
    manager = pywrapcp.RoutingIndexManager(n, 1, 0)
    routing = pywrapcp.RoutingModel(manager)

    def dist_cb(i: int, j: int) -> int:
        return matrix[manager.IndexToNode(i)][manager.IndexToNode(j)]

    cb = routing.RegisterTransitCallback(dist_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(cb)
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromMilliseconds(int(time_limit_s * 1000))
    params.log_search = False

    nn = nearest_neighbor_sequence(layout, uniq)
    index_of = {p: i for i, p in enumerate(nodes)}
    routing.CloseModelWithParameters(params)
    initial = routing.ReadAssignmentFromRoutes([[index_of[p] for p in nn]], True)
    solution = (
        routing.SolveFromAssignmentWithParameters(initial, params)
        if initial
        else routing.SolveWithParameters(params)
    )
    if solution is None:
        return nn
    seq: list[Point] = []
    idx = routing.Start(0)
    while not routing.IsEnd(idx):
        node = manager.IndexToNode(idx)
        if node != 0:
            seq.append(nodes[node])
        idx = solution.Value(routing.NextVar(idx))
    nn_len = polyline_length(_connect(layout, nn))
    return seq if polyline_length(_connect(layout, seq)) <= nn_len + EPS else nn


# --------------------------------------------------------------------------------------------- API
def route_trip(
    layout: WarehouseLayout, items: list[PickItem], algorithm: Algorithm | str, *, time_limit_s: float = 1.0
) -> TripRoute:
    algorithm = Algorithm(algorithm)
    if not items:
        return TripRoute([], [layout.depot, layout.depot], 0.0)
    groups = _group_by_point(items)
    points = list(groups)
    if algorithm is Algorithm.S_SHAPE:
        seq, wp = s_shape_sequence(layout, points)
    elif algorithm is Algorithm.LARGEST_GAP:
        seq, wp = largest_gap_sequence(layout, points)
    elif algorithm is Algorithm.NEAREST_NEIGHBOR:
        seq = nearest_neighbor_sequence(layout, points)
        wp = _connect(layout, seq)
    else:
        seq = ortools_sequence(layout, points, time_limit_s)
        wp = _connect(layout, seq)
    ordered = _items_in_visit_order(groups, seq)
    return TripRoute(ordered, wp, polyline_length(wp))


def solve_route(
    layout: WarehouseLayout,
    trips: list[list[PickItem]],
    algorithm: Algorithm | str,
    *,
    time_limit_s: float = 1.0,
) -> RouteSolution:
    sol = RouteSolution(algorithm=str(Algorithm(algorithm)))
    per_trip_limit = max(0.2, time_limit_s / max(len(trips), 1))
    for trip in trips:
        sol.trips.append(route_trip(layout, trip, algorithm, time_limit_s=per_trip_limit))
    return sol


def validate_on_graph(layout: WarehouseLayout, waypoints: list[Point]) -> bool:
    """Każdy odcinek łamanej leży na grafie: pionowy w osi alejki albo poziomy w alejce poprzecznej."""
    aisle_xs = {layout.aisle_x(a) for a in range(1, layout.aisle_count + 1)}
    for (x1, y1), (x2, y2) in zip(waypoints, waypoints[1:], strict=False):
        if abs(x1 - x2) < EPS and abs(y1 - y2) < EPS:
            continue
        if abs(x1 - x2) < EPS:
            if not any(abs(x1 - ax) < EPS for ax in aisle_xs):
                return False
            if not (layout.front_y - EPS <= min(y1, y2) and max(y1, y2) <= layout.back_y + EPS):
                return False
        elif abs(y1 - y2) < EPS:
            if not (abs(y1 - layout.front_y) < EPS or abs(y1 - layout.back_y) < EPS):
                return False
        else:
            return False
    return True


def lower_bound(layout: WarehouseLayout, points: list[Point]) -> float:
    """Dolne ograniczenie długości trasy: 2 × odległość do najdalszego punktu."""
    return 2 * max((layout.distance(layout.depot, p) for p in points), default=0.0) if points else 0.0


def gain_pct(baseline: float, value: float) -> float:
    return 0.0 if baseline <= 0 else round(100.0 * (baseline - value) / baseline, 2)


__all__ = [
    "Algorithm",
    "PickItem",
    "RouteSolution",
    "TripRoute",
    "gain_pct",
    "lower_bound",
    "polyline_length",
    "route_trip",
    "solve_route",
    "validate_on_graph",
]
