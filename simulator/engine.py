"""Silnik symulacji zdarzeń dyskretnych (SimPy) działający w procesie WMS.

Symulator wykonuje ZWOLNIONE fale: pracownicy idą po trasach (PickRoute.stops), kompletują linie,
wykrywają braki (stan fizyczny < systemowy), oddają zamówienia do pakowania (kolejka = zasób SimPy).
Równolegle generuje napływ zamówień (proces Poissona z profilem godzinowym).

Czas: `env.now` to sekundy od `base_time` (SimState.sim_now w chwili startu silnika).
Tempo: co `tick` sekund rzeczywistych symulacja przesuwa się o `tick * speed` sekund.
Zapisy do DB odbywają się pod `WRITE_LOCK` (wspólnym z API) — jeden pisarz naraz.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Generator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import simpy
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from optimizer.layout import WarehouseLayout
from simulator.orders import OrderFactory, hourly_arrival_rate
from simulator.scenarios import SCENARIOS
from wms.layout import layout_from_db
from wms.models import Order, OrderLine, PickRoute, SimEvent, SimState, StockItem, Wave, Worker
from wms.states import OrderStatus, RouteStatus, WaveStatus

log = logging.getLogger("simulator.engine")

PACK_BASE_S = 40.0
PACK_PER_LINE_S = 8.0
BREAK_AFTER_S = 3 * 3600
BREAK_S = 15 * 60
INJECTION_ARRIVAL_P = 0.01


@dataclass
class Motion:
    t0: float
    points: list[tuple[float, float]]
    seg_len: list[float]
    total: float
    speed: float

    def position(self, t: float) -> tuple[float, float]:
        if self.total <= 0:
            return self.points[-1]
        d = min(self.total, max(0.0, (t - self.t0) * self.speed))
        for (a, b), ln in zip(zip(self.points, self.points[1:], strict=False), self.seg_len, strict=False):
            if d <= ln and ln > 0:
                f = d / ln
                return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
            d -= ln
        return self.points[-1]


class SimulationEngine:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        write_lock: threading.RLock,
        *,
        tick_seconds: float = 0.5,
        orders_per_day: int | None = None,
        pack_stations: int | None = None,
    ) -> None:
        self.sf = session_factory
        self.lock = write_lock
        self.tick = tick_seconds
        self._orders_per_day_override = orders_per_day
        self._pack_override = pack_stations
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.listeners: list[Callable[[dict[str, Any]], None]] = []
        self._bootstrap()

    # ------------------------------------------------------------------ inicjalizacja
    def _bootstrap(self) -> None:
        from simulator.generate import SIZE_PARAMS

        with self.sf() as s:
            state = s.get(SimState, 1)
            if state is None:
                raise RuntimeError("Brak danych symulacji — uruchom `python -m simulator.generate`.")
            self.base_time = state.sim_now
            self.speed = state.speed
            self.running = state.running
            self.scenario = SCENARIOS.get(state.scenario, SCENARIOS["zwykly_dzien"])
            params = SIZE_PARAMS.get(state.size, SIZE_PARAMS["small"])
            self.layout: WarehouseLayout = layout_from_db(s)
            self.rng = np.random.default_rng(state.seed + 1000)
            self.order_factory = OrderFactory(s, self.rng, express_share=self.scenario.express_share)
            for w in s.execute(select(Worker)).scalars():
                w.pos_x, w.pos_y = self.layout.depot
                w.state = "idle"
            # Trasy aktywne w chwili restartu wracają do planu od bieżącego przystanku.
            for r in s.execute(
                select(PickRoute).where(PickRoute.status == RouteStatus.ACTIVE.value)
            ).scalars():
                r.status = RouteStatus.PLANNED.value
            s.commit()
        self.orders_per_day = (
            self._orders_per_day_override or params.orders_per_day
        ) * self.scenario.arrival_multiplier
        self.env = simpy.Environment()
        self.pack = simpy.Resource(self.env, capacity=self._pack_override or params.pack_stations)
        self.motions: dict[str, Motion] = {}
        self.busy: set[str] = set()
        self.worked_since_break: dict[str, float] = {}
        self.positions: dict[str, tuple[float, float, str]] = {}
        self.env.process(self._arrivals())
        self.env.process(self._dispatcher())

    def now_dt(self) -> datetime:
        return (self.base_time + timedelta(seconds=self.env.now)).replace(microsecond=0)

    # ------------------------------------------------------------------ procesy
    def _arrivals(self) -> Generator[simpy.Event, Any, None]:
        while True:
            now = self.now_dt()
            rate = hourly_arrival_rate(now.hour, self.orders_per_day)
            to_next_hour = 3600 - (now.minute * 60 + now.second)
            if rate <= 0:
                yield self.env.timeout(to_next_hour)
                continue
            dt = float(self.rng.exponential(3600.0 / rate))
            if dt >= to_next_hour:
                yield self.env.timeout(to_next_hour)  # proces bez pamięci: losujemy ponownie w nowej godzinie
                continue
            yield self.env.timeout(dt)
            with self.lock, self.sf() as s:
                self.order_factory.session = s
                note = None
                if self.rng.random() < INJECTION_ARRIVAL_P:
                    note = self.order_factory.injection_note()
                order = self.order_factory.create(self.now_dt(), note=note)
                s.commit()
                self._emit({"kind": "order_arrived", "order_id": order.id})

    def _dispatcher(self) -> Generator[simpy.Event, Any, None]:
        while True:
            with self.lock, self.sf() as s:
                rows = s.execute(
                    select(PickRoute, Wave)
                    .join(Wave, Wave.id == PickRoute.wave_id)
                    .where(
                        PickRoute.status == RouteStatus.PLANNED.value,
                        Wave.status.in_([WaveStatus.RELEASED.value, WaveStatus.IN_PROGRESS.value]),
                    )
                    .order_by(Wave.released_at, PickRoute.id)
                ).all()
                for route, wave in rows:
                    if route.worker_id in self.busy:
                        continue
                    self.busy.add(route.worker_id)
                    route.status = RouteStatus.ACTIVE.value
                    if wave.status == WaveStatus.RELEASED.value:
                        wave.status = WaveStatus.IN_PROGRESS.value
                    self.env.process(self._worker(route.id, route.worker_id))
                s.commit()
            yield self.env.timeout(10)

    def _walk(self, worker_id: str, start: tuple[float, float], end: tuple[float, float], speed: float):  # type: ignore[no-untyped-def]
        pts = self.layout.path(start, end)
        seg = [abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(pts, pts[1:], strict=False)]
        total = sum(seg)
        self.motions[worker_id] = Motion(self.env.now, pts, seg, total, speed)
        self.positions[worker_id] = (start[0], start[1], "walking")
        if total > 0:
            yield self.env.timeout(total / speed)
        self.motions.pop(worker_id, None)
        self.positions[worker_id] = (end[0], end[1], "walking")
        return total

    def _worker(self, route_id: int, worker_id: str) -> Generator[simpy.Event, Any, None]:
        with self.sf() as s:
            route = s.get(PickRoute, route_id)
            worker = s.get(Worker, worker_id)
            assert route is not None and worker is not None
            stops = list(route.stops)
            start_idx = route.progress_stop
            wave_id = route.wave_id
            speed = worker.speed_mps * worker.efficiency
            pick_time = worker.pick_time_s / worker.efficiency
        pos = self.layout.depot
        t_start = self.env.now
        for i in range(start_idx, len(stops)):
            stop = stops[i]
            target = (float(stop["x"]), float(stop["y"]))
            yield from self._walk(worker_id, pos, target, speed)
            pos = target
            if stop["type"] == "pick":
                self.positions[worker_id] = (pos[0], pos[1], "picking")
                yield self.env.timeout(pick_time * (1 + 0.1 * (int(stop["qty"]) - 1)))
                with self.lock, self.sf() as s:
                    self._do_pick(s, wave_id, worker_id, stop)
                    r = s.get(PickRoute, route_id)
                    if r is not None:
                        r.progress_stop = i + 1
                    s.commit()
            else:
                with self.lock, self.sf() as s:
                    r = s.get(PickRoute, route_id)
                    if r is not None:
                        r.progress_stop = i + 1
                    s.commit()
        if pos != self.layout.depot:
            yield from self._walk(worker_id, pos, self.layout.depot, speed)
        with self.lock, self.sf() as s:
            r = s.get(PickRoute, route_id)
            assert r is not None
            r.status = RouteStatus.DONE.value
            done_orders = self._finish_orders(s, wave_id, worker_id)
            self._maybe_complete_wave(s, wave_id)
            s.commit()
        for oid in done_orders:
            self.env.process(self._pack(oid))
        self.worked_since_break[worker_id] = self.worked_since_break.get(worker_id, 0.0) + (
            self.env.now - t_start
        )
        if self.worked_since_break[worker_id] >= BREAK_AFTER_S:
            self.positions[worker_id] = (pos[0], pos[1], "break")
            yield self.env.timeout(BREAK_S)
            self.worked_since_break[worker_id] = 0.0
        self.positions[worker_id] = (self.layout.depot[0], self.layout.depot[1], "idle")
        self.busy.discard(worker_id)

    def _do_pick(self, s: Session, wave_id: str, worker_id: str, stop: dict) -> None:
        line = s.get(OrderLine, int(stop["line_id"]))
        need = int(stop["qty"])
        stock = s.execute(
            select(StockItem).where(
                StockItem.location_id == stop["location_id"], StockItem.sku_id == stop["sku_id"]
            )
        ).scalar_one_or_none()
        physical = (
            0
            if stock is None
            else (stock.sim_physical_qty if stock.sim_physical_qty is not None else stock.qty)
        )
        picked = min(need, max(0, physical))
        if stock is not None:
            stock.reserved = max(0, stock.reserved - need)
            stock.qty = max(stock.reserved, stock.qty - picked)
            if stock.sim_physical_qty is not None:
                stock.sim_physical_qty = max(0, stock.sim_physical_qty - picked)
        if line is not None:
            line.picked_qty = picked
            line.short = picked < need
        wave = s.get(Wave, wave_id)
        if wave is not None:
            wave.picked_lines += 1
        ts = self.now_dt()
        s.add(
            SimEvent(
                ts_sim=ts,
                kind="pick",
                worker_id=worker_id,
                wave_id=wave_id,
                order_id=stop.get("order_id"),
                location_id=stop["location_id"],
                sku_id=stop["sku_id"],
                details={"qty": picked, "need": need},
            )
        )
        if picked < need:
            ev = SimEvent(
                ts_sim=ts,
                kind="stockout",
                worker_id=worker_id,
                wave_id=wave_id,
                order_id=stop.get("order_id"),
                location_id=stop["location_id"],
                sku_id=stop["sku_id"],
                details={
                    "need": need,
                    "found": int(physical),
                    "system_qty_before": int(stock.qty + picked) if stock else 0,
                },
            )
            s.add(ev)
            s.flush()
            self._emit({"kind": "stockout", "event_ref": ev.ref, "location_id": stop["location_id"]})

    def _finish_orders(self, s: Session, wave_id: str, worker_id: str) -> list[str]:
        orders = s.execute(
            select(Order).where(
                Order.wave_id == wave_id, Order.worker_id == worker_id, Order.status == "picking"
            )
        ).scalars()
        done = []
        for o in orders:
            o.status = OrderStatus.PICKED.value
            o.picked_at = self.now_dt()
            done.append(o.id)
        return done

    def _maybe_complete_wave(self, s: Session, wave_id: str) -> None:
        s.flush()  # sesje mają autoflush=False — status bieżącej trasy musi być widoczny w zapytaniu
        open_routes = s.execute(
            select(PickRoute).where(PickRoute.wave_id == wave_id, PickRoute.status != RouteStatus.DONE.value)
        ).first()
        if open_routes is None:
            wave = s.get(Wave, wave_id)
            if wave is not None and wave.status in (WaveStatus.RELEASED.value, WaveStatus.IN_PROGRESS.value):
                wave.status = WaveStatus.COMPLETED.value
                wave.completed_at = self.now_dt()
                s.add(SimEvent(ts_sim=self.now_dt(), kind="wave_completed", wave_id=wave_id, details={}))
                self._emit({"kind": "wave_completed", "wave_id": wave_id})

    def _pack(self, order_id: str) -> Generator[simpy.Event, Any, None]:
        with self.sf() as s:
            order = s.get(Order, order_id)
            lines = len(order.lines) if order else 1
        with self.pack.request() as req:
            yield req
            yield self.env.timeout(PACK_BASE_S + PACK_PER_LINE_S * lines)
        with self.lock, self.sf() as s:
            order = s.get(Order, order_id)
            if order is not None and order.status == OrderStatus.PICKED.value:
                order.status = OrderStatus.PACKED.value
                # Uproszczenie: przewoźnik odbiera paczkę od razu po spakowaniu.
                order.status = OrderStatus.SHIPPED.value
                order.shipped_at = self.now_dt()
                on_time = order.shipped_at <= order.due_at
                in_full = all(not ln.short for ln in order.lines)
                s.add(
                    SimEvent(
                        ts_sim=self.now_dt(),
                        kind="shipped",
                        order_id=order_id,
                        details={"on_time": on_time, "in_full": in_full},
                    )
                )
                s.commit()

    # ------------------------------------------------------------------ sterowanie
    def advance(self, seconds: float) -> None:
        """Przesuwa symulację o `seconds` sekund czasu symulacji (synchronicznie)."""
        with self.lock:
            self.env.run(until=self.env.now + seconds)
            self._persist()

    def _persist(self) -> None:
        with self.sf() as s:
            state = s.get(SimState, 1)
            if state is not None:
                state.sim_now = self.now_dt()
                state.pack_queue = len(self.pack.queue)
            t = self.env.now
            for w in s.execute(select(Worker)).scalars():
                motion = self.motions.get(w.id)
                if motion is not None:
                    w.pos_x, w.pos_y = motion.position(t)
                    w.state = "walking"
                elif w.id in self.positions:
                    w.pos_x, w.pos_y, w.state = self.positions[w.id]
            s.commit()

    def snapshot(self) -> dict[str, Any]:
        return {
            "sim_now": self.now_dt().isoformat(),
            "running": self.running,
            "speed": self.speed,
            "scenario": self.scenario.key,
            "pack_queue": len(self.pack.queue),
            "pack_busy": self.pack.count,
            "busy_workers": sorted(self.busy),
        }

    def set_running(self, running: bool) -> None:
        self.running = running
        with self.lock, self.sf() as s:
            state = s.get(SimState, 1)
            if state is not None:
                state.running = running
            s.commit()
        if running:
            self.start_thread()

    def set_speed(self, speed: int) -> None:
        self.speed = max(1, min(600, int(speed)))
        with self.lock, self.sf() as s:
            state = s.get(SimState, 1)
            if state is not None:
                state.speed = self.speed
            s.commit()

    def start_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="sim-engine", daemon=True)
        self._thread.start()

    def stop_thread(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.running:
                try:
                    self.advance(self.tick * self.speed)
                except Exception:  # pragma: no cover - log i kontynuacja pętli
                    log.exception("Błąd ticku symulacji")
            self._stop.wait(self.tick)

    def _emit(self, event: dict[str, Any]) -> None:
        for listener in list(self.listeners):
            try:
                listener(event)
            except Exception:  # pragma: no cover
                log.exception("listener")
