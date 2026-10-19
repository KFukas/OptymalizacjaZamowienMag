"""Bezstanowa usługa optymalizatora (HTTP). Tylko obliczenia — brak dostępu do bazy i brak zapisu.

Uruchomienie: `uvicorn optimizer.service:app --port 8100`. WMS korzysta z niej, gdy ustawiono OPTIMIZER_URL
(docker compose); w przeciwnym razie liczy w procesie (te same funkcje).
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from optimizer.forecast import forecast_sku
from optimizer.layout import WarehouseLayout
from optimizer.routing import PickItem, solve_route


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LayoutIn(Strict):
    aisle_count: Annotated[int, Field(ge=1, le=100)]
    bays: Annotated[int, Field(ge=1, le=200)]
    levels: Annotated[int, Field(ge=1, le=10)]


class ItemIn(Strict):
    location_id: str = Field(max_length=16)
    sku_id: str = Field(max_length=16)
    qty: Annotated[int, Field(ge=1, le=10_000)]
    x: float
    y: float
    order_id: str = Field(default="", max_length=16)
    line_id: int = 0
    volume_l: float = 0.0


class RouteIn(Strict):
    layout: LayoutIn
    trips: Annotated[list[Annotated[list[ItemIn], Field(max_length=2000)]], Field(max_length=50)]
    algorithm: Literal["ortools", "s_shape", "largest_gap", "nearest_neighbor"]
    time_limit_s: Annotated[float, Field(ge=0.1, le=10)] = 1.0


class ForecastIn(Strict):
    series: Annotated[dict[str, Annotated[list[float], Field(max_length=730)]], Field(max_length=500)]
    horizon_days: Annotated[int, Field(ge=1, le=28)]


app = FastAPI(title="Optimizer (bezstanowy)", version="1.0.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/route")
def route(body: RouteIn) -> dict:
    layout = WarehouseLayout(body.layout.aisle_count, body.layout.bays, body.layout.levels)
    trips = [[PickItem(**it.model_dump()) for it in trip] for trip in body.trips]
    sol = solve_route(layout, trips, body.algorithm, time_limit_s=body.time_limit_s)
    return {
        "algorithm": sol.algorithm,
        "distance_m": sol.distance_m,
        "trips": [
            {"items": [it.line_id for it in t.items], "waypoints": t.waypoints, "distance_m": t.distance_m}
            for t in sol.trips
        ],
    }


@app.post("/forecast")
def forecast(body: ForecastIn) -> dict:
    import numpy as np

    return {
        "results": [
            forecast_sku(k, np.asarray(v), body.horizon_days).as_dict()
            for k, v in sorted(body.series.items())
        ]
    }
