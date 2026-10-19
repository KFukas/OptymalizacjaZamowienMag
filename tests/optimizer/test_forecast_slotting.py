from __future__ import annotations

import numpy as np
import pytest

from optimizer.forecast import aggregate_wape, forecast_sku, naive_seasonal, wape
from optimizer.layout import WarehouseLayout
from optimizer.slotting import (
    SlotLocation,
    SlotSku,
    SlotStock,
    classify,
    compatible,
    fits,
    location_cost,
    propose_moves,
)
from optimizer.waves import AvailableWorker, OpenOrder, suggest_waves

pytestmark = pytest.mark.solver


def _seasonal(days: int = 180, level: float = 20.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    week = np.array([1.2, 1.1, 1.0, 1.0, 1.1, 0.7, 0.5])
    lam = level * np.tile(week, days // 7 + 1)[:days] * np.linspace(0.9, 1.1, days)
    return rng.poisson(lam).astype(float)


def test_forecast_shape_and_nonnegative() -> None:
    res = forecast_sku("SKU-000001", _seasonal(), horizon=14)
    assert len(res.forecast) == 14
    assert all(v >= 0 for v in res.forecast)
    assert res.wape is not None and res.wape < 0.35
    assert res.model in {"ets_holt_winters", "naive_seasonal", "mean_28d"}


def test_forecast_chooses_not_worse_than_baseline() -> None:
    res = forecast_sku("SKU-000001", _seasonal(seed=3), horizon=7)
    assert res.baseline_wape is not None
    assert res.wape <= res.baseline_wape + 1e-9


def test_intermittent_series_uses_simple_model() -> None:
    s = np.zeros(180)
    s[::9] = 1
    res = forecast_sku("SKU-000002", s, horizon=7)
    assert res.model != "ets_holt_winters"


def test_wape_and_naive() -> None:
    assert wape(np.array([1.0, 1.0]), np.array([1.0, 1.0])) == 0.0
    assert wape(np.zeros(3), np.ones(3)) is None
    assert list(naive_seasonal(np.arange(14.0), 3)) == [7.0, 8.0, 9.0]
    r = [forecast_sku("a", _seasonal(seed=1), 7), forecast_sku("b", _seasonal(level=5, seed=2), 7)]
    assert aggregate_wape(r) is not None


LAYOUT = WarehouseLayout.from_preset("small")


def _loc(aisle: int, bay: int, level: int = 1, chilled: bool = False, adr: bool = False) -> SlotLocation:
    x, y = LAYOUT.pick_point(aisle, bay)
    return SlotLocation(
        f"LOC-A{aisle:02d}-{bay:02d}-{level}", aisle, bay, level, x, y, 200, 100, chilled, adr
    )


def test_abc_xyz_and_moves() -> None:
    locs = [_loc(a, b) for a in range(1, 6) for b in range(1, 21)]
    skus = {f"SKU-{i:06d}": SlotSku(f"SKU-{i:06d}", 1.0, 0.5, False, False) for i in range(1, 11)}
    # najpopularniejszy SKU stoi najdalej
    far = [loc for loc in locs if loc.aisle == 5 and loc.bay == 20][0]
    stock = [SlotStock("SKU-000001", far.location_id, 20)] + [
        SlotStock(f"SKU-{i:06d}", locs[i * 3].location_id, 10) for i in range(2, 11)
    ]
    demand = {"SKU-000001": np.full(28, 30.0)} | {f"SKU-{i:06d}": np.full(28, 0.5) for i in range(2, 11)}
    classes = classify(demand, skus, {s.sku_id: s.qty for s in stock})
    assert classes["SKU-000001"].abc == "A"
    assert classes["SKU-000001"].xyz == "X"
    moves = propose_moves(LAYOUT, locs, skus, stock, classes, max_moves=5)
    assert moves and moves[0].sku_id == "SKU-000001"
    by_id = {loc.location_id: loc for loc in locs}
    occupied = {s.location_id for s in stock}
    targets = [m.to_location for m in moves]
    assert len(targets) == len(set(targets))
    for m in moves:
        tgt = by_id[m.to_location]
        assert m.to_location not in occupied
        assert compatible(skus[m.sku_id], tgt) and fits(skus[m.sku_id], tgt, m.qty)
        assert location_cost(LAYOUT, tgt) < location_cost(LAYOUT, by_id[m.from_location])
        assert m.gain_m_per_day > 0


def test_compatibility_rules() -> None:
    chilled_sku = SlotSku("SKU-000001", 1, 1, True, False)
    assert not compatible(chilled_sku, _loc(1, 1))
    assert compatible(chilled_sku, _loc(6, 1, chilled=True))
    adr_sku = SlotSku("SKU-000002", 1, 1, False, True)
    assert not compatible(adr_sku, _loc(6, 1, chilled=True))
    assert compatible(adr_sku, _loc(6, 15, adr=True))
    assert not fits(SlotSku("SKU-000003", 50, 1, False, False), _loc(1, 1), 5)


def test_suggest_waves_groups_by_due() -> None:
    from datetime import datetime, timedelta

    now = datetime(2026, 11, 2, 8, 0)
    orders = [
        OpenOrder(
            f"ORD-{i:08d}",
            now + timedelta(hours=2 if i < 6 else 6),
            "express" if i < 2 else "standard",
            frozenset("A"),
            2,
            5.0,
        )
        for i in range(20)
    ]
    workers = [AvailableWorker(f"WRK-{i:03d}", 3, 300) for i in range(1, 5)]
    sug = suggest_waves(orders, workers, now, max_waves=2)
    assert sug
    assert sug[0].cutoff == now + timedelta(hours=2)
    assert set(sug[0].order_ids[:6]) == {f"ORD-{i:08d}" for i in range(6)}
    assert not set(sug[0].worker_ids) & set(sug[1].worker_ids) if len(sug) > 1 else True
