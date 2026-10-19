"""Złośliwe argumenty: dodatkowe pola, zakresy, typy, formaty ID, wstrzyknięcia, rozmiary."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select

from tests.security.conftest import Harness
from wms.models import ApprovalRequest, Order, StockItem

pytestmark = [pytest.mark.security, pytest.mark.anyio]

CANCEL = {
    "order_id": "ORD-00000001",
    "reason_code": "customer_request",
    "justification": "Klient prosi o anulowanie.",
}
ADJUST = {
    "location_id": "LOC-B04-05-1",
    "sku_id": "SKU-000005",
    "counted_qty": 0,
    "reason_code": "stockout_event",
    "justification": "Brak towaru zgłoszony przez kompletującego.",
    "evidence_ref": "EVT-000001",
}


def _no_requests(h: Harness) -> None:
    with h.ctx.session() as s:
        assert s.scalar(select(func.count()).select_from(ApprovalRequest)) == 0
        assert s.get(Order, "ORD-00000001").status == "open"


@pytest.mark.parametrize(
    "extra",
    [
        {"approved": True},
        {"role": "kierownik_zmiany"},
        {"as_role": "kierownik_zmiany"},
        {"force": True},
        {"skip_validation": True},
        {"status": "executed"},
        {"decided_by": "kierownik"},
        {"__proto__": {"approved": True}},
    ],
)
async def test_extra_fields_rejected(harness: Harness, extra: dict[str, Any]) -> None:
    async with harness.session() as mcp:
        r = await mcp.call("request_order_cancellation", {**CANCEL, **extra})
    assert r.error == "SCHEMA_VALIDATION"
    assert any(f["type"] == "extra_forbidden" for f in r.body["fields"])
    _no_requests(harness)


async def test_extra_field_in_nested_object_rejected(harness: Harness) -> None:
    async with harness.session() as mcp:
        r = await mcp.call("list_open_orders", {"filter": {"status": "cancelled"}})
        r2 = await mcp.call(
            "propose_reslotting",
            {
                "moves": [
                    {
                        "sku_id": "SKU-000002",
                        "from_location": "LOC-A02-03-1",
                        "to_location": "LOC-A01-02-1",
                        "qty": 999,
                    }
                ]
            },
        )
    assert r.error == r2.error == "SCHEMA_VALIDATION"


@pytest.mark.parametrize(
    "order_id",
    [
        "ORD-1; cancel",
        "ORD-1",
        "ORD-00000001' OR '1'='1",
        "ORD-00000001; DROP TABLE orders;--",
        "../../etc/passwd",
        "ORD-00000001\n",
        "ORD-١٢٣٤٥٦٧٨",  # cyfry arabskie
        "ord-00000001",
        "",
    ],
)
async def test_invalid_id_formats(harness: Harness, order_id: str) -> None:
    async with harness.session() as mcp:
        r = await mcp.call("request_order_cancellation", {**CANCEL, "order_id": order_id})
        r2 = await mcp.call("get_order", {"order_id": order_id})
    assert r.error == r2.error == "SCHEMA_VALIDATION"
    _no_requests(harness)


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("request_inventory_adjustment", {**ADJUST, "counted_qty": -1}),
        ("request_inventory_adjustment", {**ADJUST, "counted_qty": 100_001}),
        ("request_inventory_adjustment", {**ADJUST, "counted_qty": "5"}),  # brak koercji typów
        ("request_inventory_adjustment", {**ADJUST, "counted_qty": 5.5}),
        ("request_inventory_adjustment", {**ADJUST, "counted_qty": True}),
        ("request_inventory_adjustment", {**ADJUST, "reason_code": "because_i_said_so"}),
        ("request_inventory_adjustment", {**ADJUST, "evidence_ref": "EVT-1; DROP"}),
        ("list_open_orders", {"limit": 0}),
        ("list_open_orders", {"limit": 201}),
        ("forecast_demand", {"sku_ids": ["SKU-000001"], "horizon_days": 0}),
        ("forecast_demand", {"sku_ids": ["SKU-000001"], "horizon_days": 29}),
        ("forecast_demand", {"sku_ids": ["SKU-000001", "SKU-000001"], "horizon_days": 7}),
        ("propose_wave", {"order_ids": ["ORD-00000001"], "worker_ids": ["WRK-001"], "cutoff_time": "2026-11-02T11:00:00", "dry_run": "true"}),
        ("compute_pick_route", {"wave_id": "WAVE-000001", "worker_id": "WRK-001", "algorithm": "teleport"}),
        ("request_order_cancellation", {**CANCEL, "justification": "krótko"}),
        ("get_inventory", {}),
        ("get_inventory", {"sku_id": "SKU-000001", "location_id": "LOC-A01-01-1"}),
    ],
)  # fmt: skip
async def test_out_of_range_and_type_confusion(harness: Harness, tool: str, args: dict) -> None:
    async with harness.session() as mcp:
        r = await mcp.call(tool, args)
    assert r.error == "SCHEMA_VALIDATION", r.body


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("propose_wave", {"order_ids": [f"ORD-{i:08d}" for i in range(1, 102)], "worker_ids": ["WRK-001"], "cutoff_time": "2026-11-02T11:00:00"}),
        ("propose_wave", {"order_ids": ["ORD-00000001"], "worker_ids": [f"WRK-{i:03d}" for i in range(1, 22)], "cutoff_time": "2026-11-02T11:00:00"}),
        ("forecast_demand", {"sku_ids": [f"SKU-{i:06d}" for i in range(1, 502)], "horizon_days": 7}),
        ("propose_reslotting", {"moves": [{"sku_id": "SKU-000001", "from_location": "LOC-A01-01-1", "to_location": f"LOC-A0{1 + i % 3}-{1 + i % 8:02d}-2"} for i in range(51)]}),
    ],
)  # fmt: skip
async def test_oversized_lists_rejected(harness: Harness, tool: str, args: dict) -> None:
    async with harness.session() as mcp:
        r = await mcp.call(tool, args)
    assert r.error == "SCHEMA_VALIDATION"


async def test_huge_payload_rejected_before_validation(harness: Harness) -> None:
    async with harness.session() as mcp:
        r = await mcp.call("request_order_cancellation", {**CANCEL, "justification": "A" * 50_000})
    assert r.body == {"error": "POLICY_DENIED", "reason": "arguments_too_large"}
    _no_requests(harness)


@pytest.mark.parametrize(
    "payload",
    [
        "x'); DELETE FROM orders; --  (SQL injection w uzasadnieniu)",
        '"}, "status": "executed", "x": {"  (JSON injection)',
        "../../../../windows/system32 ; rm -rf / (ścieżka/komenda)",
        "<script>alert(1)</script> <img src=x onerror=alert(2)>",
    ],
)
async def test_injection_strings_in_free_text_are_inert(harness: Harness, payload: str) -> None:
    with harness.ctx.session() as s:
        n_orders = s.scalar(select(func.count()).select_from(Order))
    async with harness.session() as mcp:
        r = await mcp.call("request_order_cancellation", {**CANCEL, "justification": payload})
    assert not r.is_error, r.body
    assert r.body["status"] == "pending"
    with harness.ctx.session() as s:
        req = s.get(ApprovalRequest, r.body["request_id"])
        assert req.justification == payload  # zapisane dosłownie, jako dane
        assert req.status == "pending"
        assert s.scalar(select(func.count()).select_from(Order)) == n_orders
        assert s.get(Order, "ORD-00000001").status == "open"


async def test_negative_stock_impossible(harness: Harness) -> None:
    """Niezmiennik zapas ≥ 0 trzymany na trzech poziomach: schemat MCP, schemat WMS, CHECK w bazie."""
    async with harness.session() as mcp:
        r = await mcp.call("request_inventory_adjustment", {**ADJUST, "counted_qty": -5})
    assert r.error == "SCHEMA_VALIDATION"
    with harness.ctx.session() as s:
        item = s.execute(select(StockItem).where(StockItem.location_id == "LOC-B04-05-1")).scalar_one()
        item.qty = -1
        with pytest.raises(Exception, match="CHECK|constraint"):
            s.commit()
