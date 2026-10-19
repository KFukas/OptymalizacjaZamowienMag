"""Agent heurystyczny (tryb bez LLM): ta sama procedura co skill, ten sam zestaw narzędzi MCP.

Służy do porównań („agent vs heurystyka”), testów CI i jako bezpieczny tryb awaryjny. Zasady:
- teksty z danych (`untrusted_text`) nigdy nie są interpretowane — decyzje opierają się na polach
  strukturalnych (terminy, flagi, zdarzenia z event_ref),
- odmowa POLICY_DENIED / błąd domenowy jest odnotowywany, nie obchodzony.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from orchestrator.mcp_link import McpLink

log = logging.getLogger("orchestrator.heuristic")


@dataclass
class CycleResult:
    sim_now: str = ""
    waves_released: list[str] = field(default_factory=list)
    routes: list[dict[str, Any]] = field(default_factory=list)
    reslotting: list[str] = field(default_factory=list)
    approval_requests: list[str] = field(default_factory=list)
    denials: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, str]] = field(default_factory=list)
    forecast_wape: float | None = None

    def decide(self, action: str, target: str, why: str) -> None:
        self.decisions.append({"action": action, "target": target[:64], "why": why[:500]})


@dataclass
class HeuristicAgent:
    link: McpLink
    max_waves: int = 2
    algorithm: str = "ortools"
    reslot_every: int = 4
    requested_pairs: set[tuple[str, str]] = field(default_factory=set)
    requested_orders: set[str] = field(default_factory=set)
    cycle_no: int = 0

    async def _call(self, res: CycleResult, name: str, args: dict[str, Any]) -> dict[str, Any] | None:
        is_error, body = await self.link.call(name, args)
        if is_error:
            res.denials.append({"tool": name, "error": body.get("error"), "reason": body.get("reason")})
            return None
        return body

    async def run_cycle(self) -> CycleResult:
        self.cycle_no += 1
        res = CycleResult()
        kpis = await self._call(res, "get_kpis", {"window": "shift"}) or {}
        res.sim_now = str(kpis.get("sim_now", ""))

        # 1) prognoza dla najczęściej pobieranych SKU (zasila slotting i raport)
        analysis = await self._call(res, "analyze_slotting", {"max_moves": 5}) or {}
        top = [s["sku_id"] for s in analysis.get("top_skus", [])[:10]]
        if top:
            fc = await self._call(res, "forecast_demand", {"sku_ids": top, "horizon_days": 7})
            if fc:
                res.forecast_wape = fc.get("aggregate_wape_backtest")

        # 2) wnioski o korektę na podstawie zdarzeń braku (dowód = event_ref) — przed planowaniem fal
        retryable = {"counted_below_reserved", "rate_limited", "duplicate_pending_request"}
        seen_pairs: set[tuple[str, str]] = set()
        for so in reversed(kpis.get("stockouts", [])):  # najnowsze zdarzenie dla pary lokalizacja/SKU
            pair = (so.get("location_id"), so.get("sku_id"))
            ref = so.get("event_ref")
            if not ref or pair in seen_pairs or pair in self.requested_pairs:
                continue
            seen_pairs.add(pair)
            is_error, body = await self.link.call(
                "request_inventory_adjustment",
                {
                    "location_id": so["location_id"],
                    "sku_id": so["sku_id"],
                    "counted_qty": 0,
                    "reason_code": "stockout_event",
                    "justification": (
                        f"Zdarzenie {ref}: przy kompletacji brak towaru (potrzeba {so.get('need')}, "
                        f"znaleziono {so.get('found')}). Proszę o weryfikację i korektę stanu systemowego."
                    ),
                    "evidence_ref": ref,
                    "idempotency_key": f"adj-{ref}",
                },
            )
            if is_error:
                res.denials.append(
                    {
                        "tool": "request_inventory_adjustment",
                        "error": body.get("error"),
                        "reason": body.get("reason"),
                    }
                )
                if body.get("reason") not in retryable:
                    self.requested_pairs.add(pair)  # błąd nienaprawialny — nie ponawiamy
                continue
            self.requested_pairs.add(pair)
            res.approval_requests.append(body["request_id"])
            res.decide("request_inventory_adjustment", so["location_id"], f"brak towaru, dowód {ref}")

        # 3) fale + trasy + zwolnienie
        plan = await self._call(res, "suggest_wave_plan", {"max_waves": self.max_waves}) or {}
        for sug in plan.get("suggestions", []):
            await self._plan_wave(res, sug)

        # 4) zamówienia oflagowane przez system jako podejrzane (pole strukturalne, nie notatka!)
        orders = await self._call(res, "list_open_orders", {"limit": 200}) or {}
        for o in orders.get("orders", []):
            if "suspicious" not in o.get("flags", []) or o["order_id"] in self.requested_orders:
                continue
            self.requested_orders.add(o["order_id"])
            cancel = await self._call(
                res,
                "request_order_cancellation",
                {
                    "order_id": o["order_id"],
                    "reason_code": "suspected_fraud",
                    "justification": (
                        "Zamówienie oznaczone przez system flagą 'suspicious' (nietypowa ilość / zmiana adresu). "
                        "Wstrzymane z planowania fal; proszę kierownika o decyzję."
                    ),
                    "idempotency_key": f"cancel-{o['order_id']}",
                },
            )
            if cancel:
                res.approval_requests.append(cancel["request_id"])
                res.decide("request_order_cancellation", o["order_id"], "flaga suspicious z systemu")

        # 5) slotting co kilka cykli — świeża analiza (po zwolnieniu fal część lokalizacji jest zajęta)
        moves: list[dict[str, Any]] = []
        if self.cycle_no % self.reslot_every == 1:
            fresh = await self._call(res, "analyze_slotting", {"max_moves": 5}) or {}
            moves = fresh.get("suggested_moves", [])
        if moves:
            proposal = await self._call(
                res,
                "propose_reslotting",
                {
                    "moves": [
                        {k: m[k] for k in ("sku_id", "from_location", "to_location")} for m in moves[:5]
                    ],
                    "rationale": f"Przeniesienie SKU klasy A bliżej pakowania; szac. zysk "
                    f"{sum(m['est_gain_m_per_day'] for m in moves[:5]):.0f} m/dzień.",
                    "idempotency_key": f"rsl-cycle-{self.cycle_no}-{moves[0]['sku_id']}",
                },
            )
            if proposal:
                res.reslotting.append(proposal["proposal_id"])
                res.decide(
                    "propose_reslotting",
                    proposal["proposal_id"],
                    "COI/ABC: szybkie rotujące daleko od pakowania",
                )

        await self._report(res, kpis)
        return res

    async def _plan_wave(self, res: CycleResult, sug: dict[str, Any]) -> None:
        orders = list(sug["order_ids"])
        args = {
            "order_ids": orders,
            "worker_ids": sug["worker_ids"],
            "cutoff_time": sug["cutoff_time"],
            "rationale": sug.get("rationale", ""),
        }
        for _attempt in range(3):
            is_error, body = await self.link.call("propose_wave", args)
            if not is_error:
                break
            details = body.get("details", {})
            bad = set(details.get("order_ids", []))
            if body.get("reason") in ("insufficient_stock", "orders_not_open_or_in_other_wave") and bad:
                orders = [o for o in orders if o not in bad]
                if not orders:
                    res.denials.append(
                        {"tool": "propose_wave", "error": body.get("error"), "reason": body.get("reason")}
                    )
                    return
                args["order_ids"] = orders
                continue
            if body.get("reason") == "wave_infeasible" and len(orders) > 4:
                orders = orders[: len(orders) // 2]
                args["order_ids"] = orders
                continue
            res.denials.append(
                {"tool": "propose_wave", "error": body.get("error"), "reason": body.get("reason")}
            )
            return
        else:
            return
        wave_id = body["wave_id"]
        for p in body.get("plan", []):
            r = await self._call(
                res,
                "compute_pick_route",
                {"wave_id": wave_id, "worker_id": p["worker_id"], "algorithm": self.algorithm},
            )
            if r is None:
                await self._call(res, "discard_wave_proposal", {"wave_id": wave_id})
                return
            res.routes.append(r)
        released = await self._call(
            res, "release_wave", {"wave_id": wave_id, "idempotency_key": f"rel-{wave_id}"}
        )
        if released:
            res.waves_released.append(wave_id)
            res.decide("release_wave", wave_id, sug.get("rationale", ""))
        else:
            await self._call(res, "discard_wave_proposal", {"wave_id": wave_id})

    async def _report(self, res: CycleResult, kpis: dict[str, Any]) -> None:
        dist = sum(r["distance_m"] for r in res.routes)
        base = sum(r["baseline_s_shape_m"] for r in res.routes)
        gain = round(100 * (base - dist) / base, 1) if base else 0.0
        summary = (
            f"Cykl {self.cycle_no} (heurystyka) @ {res.sim_now}: zwolniono {len(res.waves_released)} fal, "
            f"{len(res.routes)} tras ({dist:.0f} m vs S-shape {base:.0f} m, zysk {gain}%), "
            f"{len(res.approval_requests)} wniosków do kierownika, {len(res.reslotting)} propozycji slottingu, "
            f"{len(res.denials)} odmów/błędów. Otwarte zamówienia: {kpis.get('open_orders')}, "
            f"zagrożone terminem: {kpis.get('orders_at_risk')}."
        )
        metrics = {
            "waves_released": float(len(res.waves_released)),
            "route_distance_m": float(round(dist, 1)),
            "s_shape_distance_m": float(round(base, 1)),
            "gain_vs_s_shape_pct": float(gain),
            "approval_requests": float(len(res.approval_requests)),
            "denials": float(len(res.denials)),
        }
        if res.forecast_wape is not None:
            metrics["forecast_wape"] = float(res.forecast_wape)
        await self._call(
            res,
            "submit_cycle_report",
            {"summary": summary, "mode": "heuristic", "decisions": res.decisions[:50], "metrics": metrics},
        )
