"""Model domeny WMS (SQLAlchemy 2). Wszystkie dane są syntetyczne.

Konwencja czasu: wszystkie kolumny DateTime przechowują naiwny UTC.
- czas *domenowy* (terminy zamówień, cut-off, zdarzenia symulacji) = zegar symulacji (`SimState.sim_now`),
- czas *bezpieczeństwa* (TTL wniosków, rate limity, audyt, JWT) = zegar ścienny.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from wms.db import Base
from wms.states import (
    ApprovalStatus,
    OrderStatus,
    ReslottingStatus,
    RouteStatus,
    WaveStatus,
)


class IdCounter(Base):
    __tablename__ = "id_counters"
    name: Mapped[str] = mapped_column(String(16), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0)


class Zone(Base):
    __tablename__ = "zones"
    id: Mapped[str] = mapped_column(String(1), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))  # ambient | chilled
    adr_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    aisle_from: Mapped[int] = mapped_column(Integer)
    aisle_to: Mapped[int] = mapped_column(Integer)


class Location(Base):
    __tablename__ = "locations"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # LOC-A01-01-1
    zone_id: Mapped[str] = mapped_column(ForeignKey("zones.id"))
    aisle: Mapped[int] = mapped_column(Integer)
    bay: Mapped[int] = mapped_column(Integer)
    level: Mapped[int] = mapped_column(Integer)
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    capacity_l: Mapped[float] = mapped_column(Float)
    max_weight_kg: Mapped[float] = mapped_column(Float)
    chilled: Mapped[bool] = mapped_column(Boolean, default=False)
    adr: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (Index("ix_locations_zone_aisle", "zone_id", "aisle"),)


class Sku(Base):
    __tablename__ = "skus"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # SKU-000001
    name: Mapped[str] = mapped_column(String(200))  # tekst niezaufany
    volume_l: Mapped[float] = mapped_column(Float)
    weight_kg: Mapped[float] = mapped_column(Float)
    chilled: Mapped[bool] = mapped_column(Boolean, default=False)
    adr: Mapped[bool] = mapped_column(Boolean, default=False)
    popularity: Mapped[float] = mapped_column(Float, default=0.0)  # parametr generatora


class StockItem(Base):
    __tablename__ = "stock_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"))
    sku_id: Mapped[str] = mapped_column(ForeignKey("skus.id"))
    qty: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    # Stan fizyczny w symulacji (prawda „z hali”); None = zgodny z systemowym. Niewidoczny przez MCP.
    sim_physical_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    sku: Mapped[Sku] = relationship(lazy="joined")
    location: Mapped[Location] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint("location_id", "sku_id", name="uq_stock_loc_sku"),
        CheckConstraint("qty >= 0", name="ck_stock_qty_nonneg"),
        CheckConstraint("reserved >= 0", name="ck_stock_reserved_nonneg"),
        CheckConstraint("reserved <= qty", name="ck_stock_reserved_le_qty"),
        Index("ix_stock_sku", "sku_id"),
    )
    __mapper_args__ = {"version_id_col": version}


class Shift(Base):
    __tablename__ = "shifts"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # SHIFT-1
    name: Mapped[str] = mapped_column(String(64))
    start_hour: Mapped[int] = mapped_column(Integer)
    end_hour: Mapped[int] = mapped_column(Integer)


class Worker(Base):
    __tablename__ = "workers"
    id: Mapped[str] = mapped_column(String(8), primary_key=True)  # WRK-001
    name: Mapped[str] = mapped_column(String(64))
    shift_id: Mapped[str] = mapped_column(ForeignKey("shifts.id"))
    speed_mps: Mapped[float] = mapped_column(Float, default=1.1)
    pick_time_s: Mapped[float] = mapped_column(Float, default=12.0)
    efficiency: Mapped[float] = mapped_column(Float, default=1.0)
    cart_capacity_l: Mapped[float] = mapped_column(Float, default=400.0)
    cart_max_orders: Mapped[int] = mapped_column(Integer, default=12)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    comment: Mapped[str] = mapped_column(String(300), default="")  # tekst niezaufany
    # Pozycja na żywo (aktualizowana przez symulator).
    pos_x: Mapped[float] = mapped_column(Float, default=0.0)
    pos_y: Mapped[float] = mapped_column(Float, default=0.0)
    state: Mapped[str] = mapped_column(String(16), default="idle")  # idle|walking|picking|break|packing


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # ORD-00000001
    customer_ref: Mapped[str] = mapped_column(String(16))
    customer_note: Mapped[str] = mapped_column(Text, default="")  # tekst niezaufany
    priority: Mapped[str] = mapped_column(String(16))  # express | standard | economy
    status: Mapped[str] = mapped_column(String(16), default=OrderStatus.OPEN.value, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    wave_id: Mapped[str | None] = mapped_column(ForeignKey("waves.id"), nullable=True, index=True)
    worker_id: Mapped[str | None] = mapped_column(ForeignKey("workers.id"), nullable=True)
    picked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)

    lines: Mapped[list[OrderLine]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin", order_by="OrderLine.id"
    )
    __mapper_args__ = {"version_id_col": version}


class OrderLine(Base):
    __tablename__ = "order_lines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    sku_id: Mapped[str] = mapped_column(ForeignKey("skus.id"))
    qty: Mapped[int] = mapped_column(Integer)
    picked_qty: Mapped[int] = mapped_column(Integer, default=0)
    location_id: Mapped[str | None] = mapped_column(ForeignKey("locations.id"), nullable=True)
    short: Mapped[bool] = mapped_column(Boolean, default=False)

    order: Mapped[Order] = relationship(back_populates="lines")
    __table_args__ = (CheckConstraint("qty > 0", name="ck_line_qty_pos"),)


class Wave(Base):
    __tablename__ = "waves"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # WAVE-000001
    status: Mapped[str] = mapped_column(String(16), default=WaveStatus.VALIDATED.value, index=True)
    created_by: Mapped[str] = mapped_column(String(64))
    rationale: Mapped[str] = mapped_column(Text, default="")
    cutoff_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime)  # czas symulacji
    created_wall: Mapped[datetime] = mapped_column(DateTime)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    released_wall: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    released_by: Mapped[str | None] = mapped_column(String(96), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    est_distance_m: Mapped[float] = mapped_column(Float, default=0.0)
    est_duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    total_lines: Mapped[int] = mapped_column(Integer, default=0)
    picked_lines: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)

    assignments: Mapped[list[WaveAssignment]] = relationship(
        back_populates="wave", cascade="all, delete-orphan", lazy="selectin"
    )
    __mapper_args__ = {"version_id_col": version}


class WaveAssignment(Base):
    __tablename__ = "wave_assignments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wave_id: Mapped[str] = mapped_column(ForeignKey("waves.id"), index=True)
    worker_id: Mapped[str] = mapped_column(ForeignKey("workers.id"))
    order_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    trips: Mapped[list[list[str]]] = mapped_column(JSON, default=list)  # kursy wózka (listy zamówień)

    wave: Mapped[Wave] = relationship(back_populates="assignments")
    __table_args__ = (UniqueConstraint("wave_id", "worker_id", name="uq_wave_worker"),)


class PickRoute(Base):
    __tablename__ = "pick_routes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wave_id: Mapped[str] = mapped_column(ForeignKey("waves.id"), index=True)
    worker_id: Mapped[str] = mapped_column(ForeignKey("workers.id"))
    algorithm: Mapped[str] = mapped_column(String(16))
    distance_m: Mapped[float] = mapped_column(Float)
    duration_s: Mapped[float] = mapped_column(Float)
    baseline_distance_m: Mapped[float] = mapped_column(Float, default=0.0)  # S-shape do porównań
    trips: Mapped[int] = mapped_column(Integer, default=1)
    stops: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    path: Mapped[list[list[float]]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default=RouteStatus.PLANNED.value)
    progress_stop: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime)

    __table_args__ = (UniqueConstraint("wave_id", "worker_id", name="uq_route_wave_worker"),)


class ReslottingProposal(Base):
    __tablename__ = "reslotting_proposals"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # RSL-000001
    status: Mapped[str] = mapped_column(String(16), default=ReslottingStatus.PROPOSED.value)
    created_by: Mapped[str] = mapped_column(String(64))
    created_wall: Mapped[datetime] = mapped_column(DateTime)
    rationale: Mapped[str] = mapped_column(Text, default="")
    moves: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    est_gain_m_per_day: Mapped[float] = mapped_column(Float, default=0.0)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_wall: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __mapper_args__ = {"version_id_col": version}


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # APR-000001
    kind: Mapped[str] = mapped_column(String(32))  # order_cancellation | inventory_adjustment
    status: Mapped[str] = mapped_column(String(16), default=ApprovalStatus.PENDING.value, index=True)
    requested_by: Mapped[str] = mapped_column(String(96), index=True)
    requested_via: Mapped[str] = mapped_column(String(16))  # mcp | ui
    target_type: Mapped[str] = mapped_column(String(16))  # order | stock_item
    target_id: Mapped[str] = mapped_column(String(64))
    target_version: Mapped[int] = mapped_column(Integer)
    reason_code: Mapped[str] = mapped_column(String(32))
    justification: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    before_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    after_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    abs_delta: Mapped[int] = mapped_column(Integer, default=0)
    has_evidence: Mapped[bool] = mapped_column(Boolean, default=False)
    created_wall: Mapped[datetime] = mapped_column(DateTime, index=True)
    expires_wall: Mapped[datetime] = mapped_column(DateTime)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_wall: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed_wall: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __mapper_args__ = {"version_id_col": version}


class AuditLog(Base):
    """Dziennik append-only z łańcuchem hashy (tamper-evident)."""

    __tablename__ = "audit_log"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    source: Mapped[str] = mapped_column(String(16))  # wms | mcp
    actor: Mapped[str] = mapped_column(String(96), index=True)
    actor_type: Mapped[str] = mapped_column(String(16))  # user | service | agent | system
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(96), default="")
    args: Mapped[str] = mapped_column(Text, default="{}")  # kanoniczny JSON
    decision: Mapped[str] = mapped_column(String(8))  # allow | deny
    outcome: Mapped[str] = mapped_column(String(16))  # ok | denied | error
    reason: Mapped[str] = mapped_column(String(128), default="")
    prev_hash: Mapped[str] = mapped_column(String(64), unique=True)
    hash: Mapped[str] = mapped_column(String(64), unique=True)


GENESIS_HASH = "0" * 64


class AuditHead(Base):
    """Nagłówek łańcucha audytu: blokowany przy dopisywaniu, wykrywa obcięcie końca łańcucha."""

    __tablename__ = "audit_head"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    last_hash: Mapped[str] = mapped_column(String(64), default=GENESIS_HASH)
    count: Mapped[int] = mapped_column(Integer, default=0)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    principal: Mapped[str] = mapped_column(String(96), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_wall: Mapped[datetime] = mapped_column(DateTime)


class User(Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(32), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(32))  # operator | kierownik_zmiany
    password_hash: Mapped[str] = mapped_column(String(256))
    pin_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    pin_failures: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class SalesHistory(Base):
    __tablename__ = "sales_history"
    sku_id: Mapped[str] = mapped_column(ForeignKey("skus.id"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    qty: Mapped[int] = mapped_column(Integer)
    promo: Mapped[bool] = mapped_column(Boolean, default=False)


class SimEvent(Base):
    __tablename__ = "sim_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts_sim: Mapped[datetime] = mapped_column(DateTime, index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    worker_id: Mapped[str | None] = mapped_column(String(8), nullable=True)
    wave_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    location_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sku_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    @property
    def ref(self) -> str:
        return f"EVT-{self.id:06d}"


class SimState(Base):
    __tablename__ = "sim_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    sim_now: Mapped[datetime] = mapped_column(DateTime)
    day_start: Mapped[datetime] = mapped_column(DateTime)
    running: Mapped[bool] = mapped_column(Boolean, default=False)
    speed: Mapped[int] = mapped_column(Integer, default=10)
    scenario: Mapped[str] = mapped_column(String(32), default="zwykly_dzien")
    seed: Mapped[int] = mapped_column(Integer, default=42)
    size: Mapped[str] = mapped_column(String(8), default="small")
    pack_queue: Mapped[int] = mapped_column(Integer, default=0)


class AgentReport(Base):
    __tablename__ = "agent_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_wall: Mapped[datetime] = mapped_column(DateTime)
    sim_time: Mapped[datetime] = mapped_column(DateTime)
    author: Mapped[str] = mapped_column(String(96))
    mode: Mapped[str] = mapped_column(String(16))  # llm | heuristic
    summary: Mapped[str] = mapped_column(Text)
    decisions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
