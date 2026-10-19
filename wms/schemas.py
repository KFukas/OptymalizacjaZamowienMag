"""Schematy żądań REST. Wszystkie z `extra="forbid"` — WMS odrzuca nieznane pola niezależnie od mcp-wms."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from wms import ids

OrderId = Annotated[str, StringConstraints(pattern=ids.ORDER_ID)]
SkuId = Annotated[str, StringConstraints(pattern=ids.SKU_ID)]
LocationId = Annotated[str, StringConstraints(pattern=ids.LOCATION_ID)]
WorkerId = Annotated[str, StringConstraints(pattern=ids.WORKER_ID)]
WaveId = Annotated[str, StringConstraints(pattern=ids.WAVE_ID)]
IdemKey = Annotated[str, StringConstraints(pattern=ids.IDEMPOTENCY_KEY)]
EventRef = Annotated[str, StringConstraints(pattern=ids.EVENT_REF)]
ShortText = Annotated[str, StringConstraints(max_length=500)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginIn(Strict):
    username: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    password: Annotated[str, StringConstraints(min_length=1, max_length=128)]


class ClientCredentialsIn(Strict):
    grant_type: Literal["client_credentials"]
    client_id: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    client_secret: Annotated[str, StringConstraints(min_length=1, max_length=256)]


class CancellationRequestIn(Strict):
    order_id: OrderId
    reason_code: Literal[
        "customer_request", "suspected_fraud", "duplicate_order", "address_invalid", "unfulfillable_stock"
    ]
    justification: Annotated[str, StringConstraints(min_length=10, max_length=500)]
    idempotency_key: IdemKey | None = None


class AdjustmentRequestIn(Strict):
    location_id: LocationId
    sku_id: SkuId
    counted_qty: Annotated[int, Field(ge=0, le=100_000, strict=True)]
    reason_code: Literal["cycle_count", "damage", "stockout_event", "found_stock", "system_error"]
    justification: Annotated[str, StringConstraints(min_length=10, max_length=500)]
    evidence_ref: EventRef | None = None
    idempotency_key: IdemKey | None = None


class DecisionIn(Strict):
    decision: Literal["approve", "reject"]
    comment: Annotated[str, StringConstraints(min_length=5, max_length=500)]
    pin: Annotated[str, StringConstraints(min_length=4, max_length=12)]
    expected_version: Annotated[int, Field(ge=1, strict=True)]


class ReslottingDecisionIn(Strict):
    decision: Literal["apply", "reject"]
    comment: Annotated[str, StringConstraints(min_length=5, max_length=500)]
    expected_version: Annotated[int, Field(ge=1, strict=True)]


class WaveProposalIn(Strict):
    order_ids: Annotated[list[OrderId], Field(min_length=1, max_length=100)]
    worker_ids: Annotated[list[WorkerId], Field(min_length=1, max_length=20)]
    cutoff_time: datetime
    rationale: Annotated[str, StringConstraints(max_length=1000)] = ""
    dry_run: bool = False
    idempotency_key: IdemKey | None = None


class RouteComputeIn(Strict):
    worker_id: WorkerId
    algorithm: Literal["ortools", "s_shape", "largest_gap", "nearest_neighbor"] = "ortools"


class WaveReleaseIn(Strict):
    idempotency_key: IdemKey | None = None


class ReslottingMove(Strict):
    sku_id: SkuId
    from_location: LocationId
    to_location: LocationId


class ReslottingProposalIn(Strict):
    moves: Annotated[list[ReslottingMove], Field(min_length=1, max_length=50)]
    rationale: Annotated[str, StringConstraints(max_length=1000)] = ""
    dry_run: bool = False
    idempotency_key: IdemKey | None = None


class ForecastIn(Strict):
    sku_ids: Annotated[list[SkuId], Field(min_length=1, max_length=500)]
    horizon_days: Annotated[int, Field(ge=1, le=28, strict=True)]


class AuditAppendIn(Strict):
    """Wpis audytu od mcp-wms. Hash liczy WMS — klient nie może podać prev_hash/hash."""

    actor: Annotated[str, StringConstraints(min_length=1, max_length=96)]
    action: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    target: Annotated[str, StringConstraints(max_length=96)] = ""
    args: dict = Field(default_factory=dict)
    decision: Literal["allow", "deny"]
    outcome: Literal["ok", "denied", "error"]
    reason: Annotated[str, StringConstraints(max_length=128)] = ""


class AgentReportIn(Strict):
    summary: Annotated[str, StringConstraints(min_length=1, max_length=6000)]
    mode: Literal["llm", "heuristic"]
    decisions: Annotated[list[dict], Field(max_length=100)] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)


class SimControlIn(Strict):
    action: Literal["start", "pause", "speed", "reset", "step"]
    speed: Annotated[int, Field(ge=1, le=600)] | None = None
    scenario: (
        Literal[
            "zwykly_dzien",
            "black_friday",
            "brak_pracownikow",
            "rozbieznosc_stanow",
            "zamowienie_podejrzane",
            "notatka_injection",
        ]
        | None
    ) = None
    seed: Annotated[int, Field(ge=0, le=2**31 - 1)] | None = None
    size: Literal["small", "medium", "large"] | None = None
    minutes: Annotated[int, Field(ge=1, le=600)] | None = None
