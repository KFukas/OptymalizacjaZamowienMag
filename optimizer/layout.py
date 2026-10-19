"""Geometria magazynu i graf alejek (jeden blok, dwie alejki poprzeczne: przednia i tylna).

Model: równoległe alejki pionowe (x = const), regały po obu stronach; kompletujący stoi w osi alejki.
Przejście między alejkami jest możliwe tylko alejką poprzeczną przednią (y = front_y) lub tylną
(y = back_y). Punkt pakowania (depot) leży na alejce przedniej. Dla takiego grafu najkrótsza droga
między punktami w różnych alejkach ma postać zamkniętą:
    d = |x1 - x2| + min((y1 - f) + (y2 - f), (b - y1) + (b - y2))
co jest dokładnie odległością najkrótszej ścieżki w grafie (wierzchołki: skrzyżowania alejek
i punkty kompletacji, krawędzie: odcinki alejek). `graph_edges()` udostępnia ten graf jawnie
(np. dla frontendu i testów zgodności z Dijkstrą).
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

AISLE_SPACING_M = 3.2
FIRST_AISLE_X = 4.0
FRONT_Y = 0.0
BAY_DEPTH_M = 1.4
RACK_START_Y = 1.5  # pierwszy regał zaczyna się 1,5 m za alejką poprzeczną
BACK_MARGIN_M = 1.5


@dataclass(frozen=True)
class SizePreset:
    zones: tuple[tuple[str, str, str, int, int], ...]  # (id, nazwa, rodzaj, aisle_from, aisle_to)
    bays: int
    levels: int


SIZE_PRESETS: dict[str, SizePreset] = {
    "small": SizePreset(
        (
            ("A", "Strefa A – szybka rotacja", "ambient", 1, 3),
            ("B", "Strefa B – ogólna", "ambient", 4, 5),
            ("C", "Strefa C – chłodnia / ADR", "chilled", 6, 6),
        ),
        bays=20,
        levels=5,
    ),
    "medium": SizePreset(
        (
            ("A", "Strefa A – szybka rotacja", "ambient", 1, 8),
            ("B", "Strefa B – ogólna", "ambient", 9, 16),
            ("C", "Strefa C – chłodnia / ADR", "chilled", 17, 20),
        ),
        bays=50,
        levels=5,
    ),
    "large": SizePreset(
        (
            ("A", "Strefa A – szybka rotacja", "ambient", 1, 12),
            ("B", "Strefa B – ogólna", "ambient", 13, 24),
            ("C", "Strefa C – chłodnia / ADR", "chilled", 25, 30),
        ),
        bays=60,
        levels=6,
    ),
}


Point = tuple[float, float]


@dataclass(frozen=True)
class WarehouseLayout:
    aisle_count: int
    bays: int
    levels: int
    aisle_zone: dict[int, str] = field(default_factory=dict)
    front_y: float = FRONT_Y
    depot: Point = (0.0, FRONT_Y)

    @property
    def back_y(self) -> float:
        return RACK_START_Y + math.ceil(self.bays / 2) * BAY_DEPTH_M + BACK_MARGIN_M

    @property
    def width(self) -> float:
        return FIRST_AISLE_X + (self.aisle_count - 1) * AISLE_SPACING_M + FIRST_AISLE_X

    @staticmethod
    def from_preset(size: str) -> WarehouseLayout:
        preset = SIZE_PRESETS[size]
        aisle_zone = {a: z[0] for z in preset.zones for a in range(z[3], z[4] + 1)}
        return WarehouseLayout(len(aisle_zone), preset.bays, preset.levels, aisle_zone)

    def aisle_x(self, aisle: int) -> float:
        return FIRST_AISLE_X + (aisle - 1) * AISLE_SPACING_M

    def bay_y(self, bay: int) -> float:
        # bay nieparzysty: lewa strona, parzysty: prawa; ta sama głębokość dla pary (1,2), (3,4), ...
        return RACK_START_Y + (math.ceil(bay / 2) - 0.5) * BAY_DEPTH_M

    def pick_point(self, aisle: int, bay: int) -> Point:
        return (self.aisle_x(aisle), self.bay_y(bay))

    def rack_point(self, aisle: int, bay: int) -> Point:
        """Pozycja regału (do rysowania) — przesunięta od osi alejki."""
        side = -1.0 if bay % 2 == 1 else 1.0
        return (self.aisle_x(aisle) + side * 0.95, self.bay_y(bay))

    def aisle_of_x(self, x: float) -> int | None:
        idx = (x - FIRST_AISLE_X) / AISLE_SPACING_M
        rounded = round(idx)
        if abs(idx - rounded) < 1e-6 and 0 <= rounded < self.aisle_count:
            return int(rounded) + 1
        return None

    # --- odległości i ścieżki -----------------------------------------------------------------------
    def distance(self, p: Point, q: Point) -> float:
        (x1, y1), (x2, y2) = p, q
        if abs(x1 - x2) < 1e-9:
            return abs(y1 - y2)
        f, b = self.front_y, self.back_y
        return abs(x1 - x2) + min((y1 - f) + (y2 - f), (b - y1) + (b - y2))

    def path(self, p: Point, q: Point) -> list[Point]:
        """Punkty łamanej najkrótszej drogi p -> q (do animacji tras)."""
        (x1, y1), (x2, y2) = p, q
        if abs(x1 - x2) < 1e-9:
            return [p, q]
        f, b = self.front_y, self.back_y
        via = f if (y1 - f) + (y2 - f) <= (b - y1) + (b - y2) else b
        return [p, (x1, via), (x2, via), q]

    def distance_matrix(self, points: list[Point]) -> list[list[float]]:
        return [[self.distance(a, b) for b in points] for a in points]

    def graph_edges(self, extra_points: list[Point] | None = None) -> dict[Point, list[tuple[Point, float]]]:
        """Jawny graf: skrzyżowania alejek z alejkami poprzecznymi + punkty dodatkowe w alejkach."""
        nodes_by_aisle: dict[float, set[float]] = {}
        for a in range(1, self.aisle_count + 1):
            nodes_by_aisle[self.aisle_x(a)] = {self.front_y, self.back_y}
        cross: dict[float, set[float]] = {self.front_y: set(), self.back_y: set()}
        for x in nodes_by_aisle:
            cross[self.front_y].add(x)
            cross[self.back_y].add(x)
        for px, py in [*(extra_points or []), self.depot]:
            if px in nodes_by_aisle:
                nodes_by_aisle[px].add(py)
            elif py in cross:
                cross[py].add(px)
        adj: dict[Point, list[tuple[Point, float]]] = {}

        def link(a: Point, b: Point) -> None:
            d = abs(a[0] - b[0]) + abs(a[1] - b[1])
            adj.setdefault(a, []).append((b, d))
            adj.setdefault(b, []).append((a, d))

        for x, ys in nodes_by_aisle.items():
            s = sorted(ys)
            for y1, y2 in zip(s, s[1:], strict=False):
                link((x, y1), (x, y2))
        for y, xs in cross.items():
            s = sorted(xs)
            for x1, x2 in zip(s, s[1:], strict=False):
                link((x1, y), (x2, y))
        return adj


def dijkstra(adj: dict[Point, list[tuple[Point, float]]], src: Point, dst: Point) -> float:
    dist = {src: 0.0}
    heap = [(0.0, src)]
    while heap:
        d, u = heapq.heappop(heap)
        if u == dst:
            return d
        if d > dist.get(u, math.inf):
            continue
        for v, w in adj.get(u, []):
            nd = d + w
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                heapq.heappush(heap, (nd, v))
    return math.inf


def parse_location_id(location_id: str) -> tuple[str, int, int, int]:
    """LOC-A01-02-3 -> ("A", 1, 2, 3)."""
    zone = location_id[4]
    aisle = int(location_id[5:7])
    bay = int(location_id[8:10])
    level = int(location_id[11])
    return zone, aisle, bay, level
