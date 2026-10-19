"""Walidacja domenowa po stronie serwera: kolejność wywołań, stany, niezmienniki (fale, slotting)."""

from __future__ import annotations

import pytest

from tests.security.conftest import Harness

pytestmark = [pytest.mark.security, pytest.mark.anyio]

WAVE = {
    "order_ids": ["ORD-00000001", "ORD-00000002"],
    "worker_ids": ["WRK-001"],
    "cutoff_time": "2026-11-02T11:30:00",
    "rationale": "test",
}


async def test_out_of_order_calls_are_rejected(harness: Harness) -> None:
    async with harness.session() as mcp:
        # trasa i zwolnienie dla nieistniejącej fali
        r = await mcp.call("compute_pick_route", {"wave_id": "WAVE-000777", "worker_id": "WRK-001"})
        assert r.body["error"] == "NOT_FOUND"
        r = await mcp.call("release_wave", {"wave_id": "WAVE-000777"})
        assert r.body["error"] == "NOT_FOUND"
        wid = (await mcp.call("propose_wave", WAVE)).body["wave_id"]
        # zwolnienie przed wyznaczeniem tras
        r = await mcp.call("release_wave", {"wave_id": wid})
        assert r.body["reason"] == "routes_missing"
        assert not (await mcp.call("compute_pick_route", {"wave_id": wid, "worker_id": "WRK-001"})).is_error
        # trasa dla pracownika spoza fali
        r = await mcp.call("compute_pick_route", {"wave_id": wid, "worker_id": "WRK-003"})
        assert r.body["error"] == "NOT_FOUND"
        assert not (await mcp.call("release_wave", {"wave_id": wid})).is_error
        # po zwolnieniu: brak zmiany trasy, brak ponownego zwolnienia, brak wycofania
        assert (await mcp.call("compute_pick_route", {"wave_id": wid, "worker_id": "WRK-001"})).body[
            "reason"
        ] == "wave_not_validated"
        assert (await mcp.call("release_wave", {"wave_id": wid})).body["reason"] == "wave_not_validated"
        assert (await mcp.call("discard_wave_proposal", {"wave_id": wid})).body[
            "reason"
        ] == "wave_not_validated"
        # anulowanie zamówienia w realizacji jest niemożliwe nawet jako wniosek
        r = await mcp.call(
            "request_order_cancellation",
            {
                "order_id": "ORD-00000001",
                "reason_code": "customer_request",
                "justification": "Klient prosi o anulowanie.",
            },
        )
        assert r.body["reason"] == "order_not_cancellable"


async def test_wave_with_assigned_or_cancelled_order(harness: Harness) -> None:
    async with harness.session() as mcp:
        assert not (await mcp.call("propose_wave", WAVE)).is_error
        r = await mcp.call("propose_wave", {**WAVE, "worker_ids": ["WRK-002"]})
        assert r.body["reason"] == "orders_not_open_or_in_other_wave"
        rid = (
            await mcp.call(
                "request_order_cancellation",
                {
                    "order_id": "ORD-00000005",
                    "reason_code": "customer_request",
                    "justification": "Klient prosi o anulowanie.",
                },
            )
        ).body["request_id"]
    harness.ctx.client.post(
        f"/approvals/{rid}/decide",
        json={"decision": "approve", "comment": "Potwierdzone.", "pin": "2468", "expected_version": 1},
        headers=harness.ctx.manager(),
    )
    async with harness.session() as mcp:
        r = await mcp.call("propose_wave", {**WAVE, "order_ids": ["ORD-00000005"], "worker_ids": ["WRK-003"]})
    assert r.body["reason"] == "orders_not_open_or_in_other_wave"


async def test_wave_capacity_and_shift_checks(harness: Harness) -> None:
    async with harness.session() as mcp:
        r = await mcp.call("propose_wave", {**WAVE, "worker_ids": ["WRK-009"]})
        assert r.body["reason"] == "workers_not_on_shift"
        r = await mcp.call("propose_wave", {**WAVE, "cutoff_time": "2026-11-02T08:00:30"})
        assert r.body["reason"] == "wave_infeasible"
        r = await mcp.call("propose_wave", {**WAVE, "cutoff_time": "2026-11-03T08:00:00"})
        assert r.body["reason"] == "cutoff_too_far"


@pytest.mark.parametrize(
    ("move", "reason"),
    [
        (
            {"sku_id": "SKU-000002", "from_location": "LOC-A02-03-1", "to_location": "LOC-A01-01-1"},
            "target_occupied",
        ),
        (
            {"sku_id": "SKU-000003", "from_location": "LOC-C06-02-1", "to_location": "LOC-A01-02-1"},
            "incompatible_location",
        ),
        (
            {"sku_id": "SKU-000002", "from_location": "LOC-A02-03-1", "to_location": "LOC-C06-03-1"},
            "incompatible_location",
        ),
        (
            {"sku_id": "SKU-000002", "from_location": "LOC-A03-03-1", "to_location": "LOC-A01-02-1"},
            "no_stock_at_source",
        ),
        (
            {"sku_id": "SKU-000099", "from_location": "LOC-A02-03-1", "to_location": "LOC-A01-02-1"},
            "sku_not_found",
        ),
        (
            {"sku_id": "SKU-000002", "from_location": "LOC-A02-03-1", "to_location": "LOC-Z99-01-1"},
            "location_not_found",
        ),
    ],
)
async def test_reslotting_to_full_or_incompatible_location(harness: Harness, move: dict, reason: str) -> None:
    async with harness.session() as mcp:
        r = await mcp.call("propose_reslotting", {"moves": [move], "rationale": "test"})
    assert r.error == "DOMAIN_VALIDATION"
    assert r.body["details"]["errors"][0]["reason"] == reason


async def test_reslotting_collision_with_active_wave(harness: Harness) -> None:
    async with harness.session() as mcp:
        assert not (await mcp.call("propose_wave", WAVE)).is_error
        r = await mcp.call(
            "propose_reslotting",
            {
                "moves": [
                    {"sku_id": "SKU-000002", "from_location": "LOC-A02-03-1", "to_location": "LOC-A01-02-1"}
                ]
            },
        )
    assert r.body["details"]["errors"][0]["reason"] == "location_in_active_wave"


async def test_agent_cannot_apply_own_reslotting(harness: Harness) -> None:
    async with harness.session() as mcp:
        r = await mcp.call(
            "propose_reslotting",
            {
                "moves": [
                    {"sku_id": "SKU-000002", "from_location": "LOC-A02-03-1", "to_location": "LOC-A01-02-1"}
                ]
            },
        )
        pid = r.body["proposal_id"]
    resp = harness.ctx.client.post(
        f"/slotting/proposals/{pid}/decide",
        json={"decision": "apply", "comment": "samozatwierdzenie", "expected_version": 1},
        headers=harness.ctx.service(),
    )
    assert resp.status_code == 403
