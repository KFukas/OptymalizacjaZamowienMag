from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from simulator.engine import SimulationEngine
from simulator.generate import generate
from tests.conftest import MANAGER_PASSWORD, Ctx, make_settings
from wms.db import make_engine
from wms.main import create_app
from wms.models import Order, StockItem, Wave

CUTOFF = "2026-11-02T11:30:00"
ORDERS = [f"ORD-{i:08d}" for i in (1, 2, 6, 7, 8)]


def _propose(ctx: Ctx, h: dict, **over: object):  # type: ignore[no-untyped-def]
    body = {
        "order_ids": ORDERS,
        "worker_ids": ["WRK-001", "WRK-002"],
        "cutoff_time": CUTOFF,
        "rationale": "test",
    }
    body.update(over)
    return ctx.client.post("/waves", json=body, headers=h)


def test_full_wave_lifecycle_with_simulation(ctx: Ctx) -> None:
    h = ctx.service()
    r = _propose(ctx, h)
    assert r.status_code == 201, r.text
    wave_id = r.json()["wave_id"]
    plan = r.json()["plan"]
    assert sorted(o for p in plan for t in p["trips"] for o in t) == sorted(ORDERS)
    with ctx.session() as s:
        assert {s.get(Order, o).status for o in ORDERS} == {"assigned"}
    # zwolnienie bez tras -> odmowa
    assert (
        ctx.client.post(f"/waves/{wave_id}/release", json={}, headers=h).json()["reason"] == "routes_missing"
    )
    for p in plan:
        rr = ctx.client.post(
            f"/waves/{wave_id}/routes", json={"worker_id": p["worker_id"], "algorithm": "ortools"}, headers=h
        )
        assert rr.status_code == 200, rr.text
        assert rr.json()["distance_m"] <= rr.json()["baseline_s_shape_m"] + 1e-6
    r = ctx.client.post(f"/waves/{wave_id}/release", json={"idempotency_key": "release-0001"}, headers=h)
    assert r.status_code == 200, r.text
    again = ctx.client.post(f"/waves/{wave_id}/release", json={"idempotency_key": "release-0001"}, headers=h)
    assert again.json().get("idempotent_replay") is True
    with ctx.session() as s:
        reserved = (
            s.execute(select(StockItem).where(StockItem.location_id == "LOC-A01-01-1")).scalar_one().reserved
        )
        assert reserved == 4  # ORD-1,6,7,8 po 1 szt.
    sim = SimulationEngine(ctx.app.state.session_factory, threading.RLock(), tick_seconds=0.01)  # type: ignore[attr-defined]
    sim.advance(2 * 3600)
    with ctx.session() as s:
        assert s.get(Wave, wave_id).status == "completed"
        assert {s.get(Order, o).status for o in ORDERS} == {"shipped"}
    detail = ctx.client.get(f"/waves/{wave_id}", headers=h).json()
    assert detail["progress"] == 1.0 and len(detail["routes"]) == len(plan)


def test_wave_validation_errors(ctx: Ctx) -> None:
    h = ctx.service()
    assert _propose(ctx, h, cutoff_time="2026-11-02T07:00:00").json()["reason"] == "cutoff_in_past"
    assert _propose(ctx, h, worker_ids=["WRK-009"]).json()["reason"] == "workers_not_on_shift"
    assert _propose(ctx, h, order_ids=["ORD-00000001", "ORD-00000001"]).json()["reason"] == "duplicate_ids"
    assert _propose(ctx, h, order_ids=["ORD-99999999"]).json()["reason"] == "orders_not_found"
    assert _propose(ctx, h, worker_ids=["WRK-001", "WRK-777"]).json()["reason"] == "workers_not_found"
    dry = _propose(ctx, h, dry_run=True)
    assert dry.status_code == 201 and dry.json()["wave_id"] is None
    with ctx.session() as s:
        assert s.get(Order, "ORD-00000001").status == "open"
    assert _propose(ctx, h).status_code == 201
    r = _propose(ctx, h, worker_ids=["WRK-003"])
    assert r.json()["reason"] == "orders_not_open_or_in_other_wave"
    r = _propose(ctx, h, order_ids=["ORD-00000009"], worker_ids=["WRK-001"])
    assert r.json()["reason"] == "workers_busy_in_active_wave"


def test_insufficient_stock(ctx: Ctx) -> None:
    with ctx.session() as s:
        for it in s.execute(select(StockItem).where(StockItem.sku_id == "SKU-000002")).scalars():
            it.qty = 1
        s.commit()
    r = _propose(ctx, ctx.service(), order_ids=["ORD-00000002"])
    assert r.json()["reason"] == "insufficient_stock"


def test_discard_returns_orders(ctx: Ctx) -> None:
    h = ctx.service()
    wid = _propose(ctx, h).json()["wave_id"]
    assert ctx.client.post(f"/waves/{wid}/discard", headers=h).json()["status"] == "discarded"
    with ctx.session() as s:
        assert {s.get(Order, o).status for o in ORDERS} == {"open"}


def test_cancellation_removes_order_from_validated_wave(ctx: Ctx) -> None:
    h = ctx.service()
    wid = _propose(ctx, h).json()["wave_id"]
    rid = ctx.client.post(
        "/approvals/order-cancellation",
        json={
            "order_id": "ORD-00000002",
            "reason_code": "customer_request",
            "justification": "Klient zrezygnował telefonicznie.",
        },
        headers=h,
    ).json()["request_id"]
    r = ctx.client.post(
        f"/approvals/{rid}/decide",
        json={"decision": "approve", "comment": "Potwierdzone.", "pin": "2468", "expected_version": 1},
        headers=ctx.manager(),
    )
    assert r.status_code == 200, r.text
    with ctx.session() as s:
        assert s.get(Order, "ORD-00000002").status == "cancelled"
        assert all("ORD-00000002" not in a.order_ids for a in s.get(Wave, wid).assignments)


MOVE_OK = {"sku_id": "SKU-000002", "from_location": "LOC-A02-03-1", "to_location": "LOC-A01-02-1"}


def test_reslotting_validation_and_apply(ctx: Ctx) -> None:
    h = ctx.service()

    def propose(moves: list[dict], **kw: object):  # type: ignore[no-untyped-def]
        return ctx.client.post(
            "/slotting/proposals", json={"moves": moves, "rationale": "test", **kw}, headers=h
        )

    cases = [
        ({**MOVE_OK, "to_location": "LOC-A01-01-1"}, "target_occupied"),
        (
            {"sku_id": "SKU-000003", "from_location": "LOC-C06-02-1", "to_location": "LOC-A01-02-1"},
            "incompatible_location",
        ),
        (
            {"sku_id": "SKU-000004", "from_location": "LOC-C06-06-1", "to_location": "LOC-C06-01-1"},
            "incompatible_location",
        ),
        ({**MOVE_OK, "from_location": "LOC-A01-03-1"}, "no_stock_at_source"),
        ({**MOVE_OK, "to_location": MOVE_OK["from_location"]}, "same_location"),
    ]
    for move, reason in cases:
        r = propose([move])
        assert r.status_code == 422, (move, r.text)
        assert r.json()["details"]["errors"][0]["reason"] == reason
    dup = propose([MOVE_OK, {**MOVE_OK, "sku_id": "SKU-000001", "from_location": "LOC-B05-07-2"}])
    assert dup.json()["details"]["errors"][0]["reason"] == "duplicate_target"
    with ctx.session() as s:
        it = s.execute(select(StockItem).where(StockItem.location_id == "LOC-A02-03-1")).scalar_one()
        it.qty = 400  # 400 × 2 l > 200 l
        s.commit()
    assert propose([MOVE_OK]).json()["details"]["errors"][0]["reason"] == "capacity_exceeded"
    with ctx.session() as s:
        it = s.execute(select(StockItem).where(StockItem.location_id == "LOC-A02-03-1")).scalar_one()
        it.qty = 25
        s.commit()
    r = propose([MOVE_OK])
    assert r.status_code == 201, r.text
    pid = r.json()["proposal_id"]
    assert r.json()["moves"][0]["est_gain_m_per_day"] >= 0
    assert (
        ctx.client.post(
            f"/slotting/proposals/{pid}/decide",
            json={"decision": "apply", "comment": "ok, przenosimy", "expected_version": 1},
            headers=h,
        ).status_code
        == 403
    )
    r = ctx.client.post(
        f"/slotting/proposals/{pid}/decide",
        json={"decision": "apply", "comment": "ok, przenosimy", "expected_version": 1},
        headers=ctx.manager(),
    )
    assert r.status_code == 200 and r.json()["status"] == "applied"
    with ctx.session() as s:
        assert (
            s.execute(select(StockItem).where(StockItem.location_id == "LOC-A01-02-1")).scalar_one().qty == 25
        )
        assert s.get(Order, "ORD-00000002").lines[0].location_id == "LOC-A01-02-1"


def test_reslotting_blocked_by_active_wave(ctx: Ctx) -> None:
    h = ctx.service()
    assert _propose(ctx, h).status_code == 201
    r = ctx.client.post("/slotting/proposals", json={"moves": [MOVE_OK]}, headers=h)
    assert r.json()["details"]["errors"][0]["reason"] == "location_in_active_wave"


@pytest.fixture(scope="module")
def generated() -> TestClient:
    engine = make_engine("sqlite:///:memory:")
    generate(engine, seed=42, size="small")
    app = create_app(make_settings(), engine)
    return TestClient(app)


def _mgr(client: TestClient) -> dict:
    tok = client.post("/auth/login", json={"username": "kierownik", "password": MANAGER_PASSWORD}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {tok}"}


def test_forecast_slotting_suggestions_on_generated_data(generated: TestClient) -> None:
    h = _mgr(generated)
    top = generated.get("/slotting/analysis", headers=h).json()
    assert top["abc_counts"]["A"] > 0
    assert top["suggested_moves"], "startowy slotting jest celowo nieoptymalny"
    skus = [t["sku_id"] for t in top["top_skus"][:5]]
    fc = generated.post("/forecast", json={"sku_ids": skus, "horizon_days": 7}, headers=h).json()
    assert len(fc["results"]) == 5 and all(len(r["forecast"]) == 7 for r in fc["results"])
    assert any(r["model"] == "ets_holt_winters" for r in fc["results"])
    sug = generated.get("/planning/wave-suggestions", headers=h).json()
    assert sug["suggestions"] and sug["suggestions"][0]["worker_ids"]
    hm = generated.get("/layout/heatmap", params={"metric": "rotation"}, headers=h).json()
    assert hm["max"] > 0
    assert (
        generated.post(
            "/forecast", json={"sku_ids": ["SKU-999999"], "horizon_days": 7}, headers=h
        ).status_code
        == 422
    )
    assert (
        generated.post("/forecast", json={"sku_ids": skus, "horizon_days": 29}, headers=h).status_code == 422
    )


def test_location_with_reported_stockout_is_quarantined(ctx: Ctx) -> None:
    # w danych testowych jest zdarzenie braku EVT-000001 dla LOC-B04-05-1 / SKU-000005
    r = _propose(ctx, ctx.service(), order_ids=["ORD-00000005"], worker_ids=["WRK-001"])
    assert r.json()["reason"] == "insufficient_stock"
