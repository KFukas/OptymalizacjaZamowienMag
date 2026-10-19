"""Schematy wejścia narzędzi mcp-wms: wąskie typy, enumy, zakresy, wzorce ID, limity list, `extra="forbid"`.

Walidacja odbywa się w trybie ŚCISŁYM (strict) na surowym JSON — brak koercji typów („5” ≠ 5, „true” ≠ true).
Wzorce ID używają `[0-9]`, a nie `\\d` (który dopasowuje też cyfry Unicode, np. arabskie).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

ORDER_ID = r"^ORD-[0-9]{8}$"
SKU_ID = r"^SKU-[0-9]{6}$"
LOCATION_ID = r"^LOC-[A-Z][0-9]{2}-[0-9]{2}-[0-9]$"
WORKER_ID = r"^WRK-[0-9]{3}$"
WAVE_ID = r"^WAVE-[0-9]{6}$"
APPROVAL_ID = r"^APR-[0-9]{6}$"
SHIFT_ID = r"^SHIFT-[0-9]$"
EVENT_REF = r"^EVT-[0-9]{6,9}$"
IDEMPOTENCY_KEY = r"^[A-Za-z0-9_-]{8,64}$"

OrderId = Annotated[str, StringConstraints(pattern=ORDER_ID)]
SkuId = Annotated[str, StringConstraints(pattern=SKU_ID)]
LocationId = Annotated[str, StringConstraints(pattern=LOCATION_ID)]
WorkerId = Annotated[str, StringConstraints(pattern=WORKER_ID)]
WaveId = Annotated[str, StringConstraints(pattern=WAVE_ID)]
ApprovalId = Annotated[str, StringConstraints(pattern=APPROVAL_ID)]
IdemKey = Annotated[str, StringConstraints(pattern=IDEMPOTENCY_KEY)]
Text500 = Annotated[str, StringConstraints(min_length=10, max_length=500)]
Rationale = Annotated[str, StringConstraints(max_length=1000)]


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


# ----------------------------------------------------------------------------------------------- odczyt
class OrderFilter(ToolInput):
    priority: Literal["express", "standard", "economy"] | None = None
    due_before: datetime | None = None
    zone: Annotated[str, StringConstraints(pattern=r"^[A-Z]$")] | None = None


class ListOpenOrdersIn(ToolInput):
    filter: OrderFilter | None = None
    limit: Annotated[int, Field(ge=1, le=200)] = 50


class GetOrderIn(ToolInput):
    order_id: OrderId


class GetInventoryIn(ToolInput):
    sku_id: SkuId | None = None
    location_id: LocationId | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.sku_id is None) == (self.location_id is None):
            raise ValueError("exactly_one_of_sku_id_location_id")
        return self


class GetLayoutIn(ToolInput):
    include_locations: bool = False


class ListWorkersIn(ToolInput):
    shift_id: Annotated[str, StringConstraints(pattern=SHIFT_ID)] | None = None


class GetKpisIn(ToolInput):
    window: Literal["15m", "1h", "shift", "day"] = "1h"


class GetWaveIn(ToolInput):
    wave_id: WaveId


class SuggestWavePlanIn(ToolInput):
    max_waves: Annotated[int, Field(ge=1, le=5)] = 3


class ForecastDemandIn(ToolInput):
    sku_ids: Annotated[list[SkuId], Field(min_length=1, max_length=500)]
    horizon_days: Annotated[int, Field(ge=1, le=28)]

    @model_validator(mode="after")
    def _unique(self) -> Self:
        if len(set(self.sku_ids)) != len(self.sku_ids):
            raise ValueError("duplicate_ids")
        return self


class AnalyzeSlottingIn(ToolInput):
    max_moves: Annotated[int, Field(ge=1, le=50)] = 20


class GetApprovalStatusIn(ToolInput):
    request_id: ApprovalId


# ----------------------------------------------------------------------------------------------- propozycje
class ProposeWaveIn(ToolInput):
    order_ids: Annotated[list[OrderId], Field(min_length=1, max_length=100)]
    worker_ids: Annotated[list[WorkerId], Field(min_length=1, max_length=20)]
    cutoff_time: datetime
    rationale: Rationale = ""
    dry_run: bool = False
    idempotency_key: IdemKey | None = None


class DiscardWaveProposalIn(ToolInput):
    wave_id: WaveId


class ComputePickRouteIn(ToolInput):
    wave_id: WaveId
    worker_id: WorkerId
    algorithm: Literal["ortools", "s_shape", "largest_gap"] = "ortools"


class ReleaseWaveIn(ToolInput):
    wave_id: WaveId
    idempotency_key: IdemKey | None = None


class Move(ToolInput):
    sku_id: SkuId
    from_location: LocationId
    to_location: LocationId


class ProposeReslottingIn(ToolInput):
    moves: Annotated[list[Move], Field(min_length=1, max_length=50)]
    rationale: Rationale = ""
    dry_run: bool = False
    idempotency_key: IdemKey | None = None


class Decision(ToolInput):
    action: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    target: Annotated[str, StringConstraints(max_length=64)] = ""
    why: Annotated[str, StringConstraints(max_length=500)] = ""


class SubmitCycleReportIn(ToolInput):
    summary: Annotated[str, StringConstraints(min_length=1, max_length=4000)]
    mode: Literal["llm", "heuristic"] = "llm"
    decisions: Annotated[list[Decision], Field(max_length=50)] = []
    metrics: Annotated[
        dict[Annotated[str, StringConstraints(max_length=40)], float], Field(max_length=30)
    ] = {}


# ----------------------------------------------------------------------------------------------- wnioski
class RequestOrderCancellationIn(ToolInput):
    order_id: OrderId
    reason_code: Literal[
        "customer_request", "suspected_fraud", "duplicate_order", "address_invalid", "unfulfillable_stock"
    ]
    justification: Text500
    idempotency_key: IdemKey | None = None


class RequestInventoryAdjustmentIn(ToolInput):
    location_id: LocationId
    sku_id: SkuId
    counted_qty: Annotated[int, Field(ge=0, le=100_000)]
    reason_code: Literal["cycle_count", "damage", "stockout_event", "found_stock", "system_error"]
    justification: Text500
    evidence_ref: Annotated[str, StringConstraints(pattern=EVENT_REF)] | None = None
    idempotency_key: IdemKey | None = None
