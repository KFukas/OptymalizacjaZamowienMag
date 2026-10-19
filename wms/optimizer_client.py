"""Adapter optymalizatora: obliczenia w procesie albo przez bezstanową usługę HTTP (OPTIMIZER_URL)."""

from __future__ import annotations

import httpx

from optimizer.layout import WarehouseLayout
from optimizer.routing import PickItem, RouteSolution, TripRoute
from optimizer.routing import solve_route as local_solve
from wms.config import Settings

ROUTE_TIME_LIMIT_S = 1.0


def solve_route(
    settings: Settings, layout: WarehouseLayout, trips: list[list[PickItem]], algorithm: str
) -> RouteSolution:
    if not settings.optimizer_url:
        return local_solve(layout, trips, algorithm, time_limit_s=ROUTE_TIME_LIMIT_S)
    payload = {
        "layout": {"aisle_count": layout.aisle_count, "bays": layout.bays, "levels": layout.levels},
        "trips": [
            [
                {
                    "location_id": i.location_id,
                    "sku_id": i.sku_id,
                    "qty": i.qty,
                    "x": i.x,
                    "y": i.y,
                    "order_id": i.order_id,
                    "line_id": i.line_id,
                    "volume_l": i.volume_l,
                }
                for i in trip
            ]
            for trip in trips
        ],
        "algorithm": algorithm,
        "time_limit_s": ROUTE_TIME_LIMIT_S,
    }
    try:
        resp = httpx.post(f"{settings.optimizer_url.rstrip('/')}/route", json=payload, timeout=30)
        resp.raise_for_status()
    except httpx.HTTPError:
        # Usługa niedostępna — liczymy lokalnie (te same funkcje), żeby nie blokować planowania.
        return local_solve(layout, trips, algorithm, time_limit_s=ROUTE_TIME_LIMIT_S)
    data = resp.json()
    sol = RouteSolution(algorithm=data["algorithm"])
    for trip_items, t in zip(trips, data["trips"], strict=True):
        by_line = {i.line_id: i for i in trip_items}
        sol.trips.append(
            TripRoute(
                items=[by_line[lid] for lid in t["items"]],
                waypoints=[(float(p[0]), float(p[1])) for p in t["waypoints"]],
                distance_m=float(t["distance_m"]),
            )
        )
    return sol
