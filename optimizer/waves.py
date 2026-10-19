"""Sugestie fal kompletacji (czysto obliczeniowe): grupowanie po terminie (cut-off przewoźnika), priorytecie
i strefach, z limitem wielkości wynikającym z liczby dostępnych pracowników i pojemności wózków."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta


@dataclass(frozen=True)
class OpenOrder:
    order_id: str
    due_at: datetime
    priority: str
    zones: frozenset[str]
    lines: int
    volume_l: float


@dataclass(frozen=True)
class AvailableWorker:
    worker_id: str
    cart_max_orders: int
    cart_capacity_l: float


@dataclass
class WaveSuggestion:
    order_ids: list[str]
    worker_ids: list[str]
    cutoff: datetime
    zones: list[str]
    rationale: str
    lines: int = 0
    volume_l: float = 0.0
    tags: list[str] = field(default_factory=list)


PRIORITY_RANK = {"express": 0, "standard": 1, "economy": 2}


CARRIER_DEPARTURES = (10, 14, 18, 21)


def next_departure(earliest: datetime, latest: datetime) -> datetime:
    """Najbliższy odjazd przewoźnika nie wcześniej niż `earliest`; gdy wypada po `latest` — `latest`."""
    day = earliest.replace(minute=0, second=0, microsecond=0)
    for offset in range(2):
        for hour in CARRIER_DEPARTURES:
            cand = (day + timedelta(days=offset)).replace(hour=hour)
            if cand >= earliest:
                return min(cand, latest)
    return latest


def suggest_waves(
    orders: list[OpenOrder],
    workers: list[AvailableWorker],
    now: datetime,
    *,
    max_waves: int = 3,
    trips_per_worker: int = 2,
    max_orders_per_wave: int = 100,
    horizon: timedelta = timedelta(hours=12),
    min_lead: timedelta = timedelta(minutes=45),
) -> list[WaveSuggestion]:
    if not orders or not workers:
        return []
    # cut-off fali musi mieścić się w horyzoncie planowania (WMS odrzuca cut-off > 12 h)
    orders = [o for o in orders if o.due_at <= now + horizon]
    # Zamówienia po terminie (lub tuż przed nim) jadą najbliższym możliwym odjazdem — inaczej sugestia miałaby
    # cut-off w przeszłości i WMS słusznie by ją odrzucił. Zachowujemy je na początku kolejki (są najpilniejsze).
    catch_up = next_departure(now + min_lead, now + horizon - timedelta(minutes=5))
    overdue_ids = {o.order_id for o in orders if o.due_at < now + min_lead}
    orders = [replace(o, due_at=catch_up) if o.order_id in overdue_ids else o for o in orders]
    pool = sorted(
        orders,
        key=lambda o: (o.due_at, o.order_id not in overdue_ids, PRIORITY_RANK.get(o.priority, 9), o.order_id),
    )
    free = sorted(workers, key=lambda w: w.worker_id)
    suggestions: list[WaveSuggestion] = []
    while pool and free and len(suggestions) < max_waves:
        cutoff = pool[0].due_at
        same_due = [o for o in pool if o.due_at == cutoff]
        # do najwcześniejszego terminu dobieramy zamówienia z kolejnych terminów, jeśli zostaje pojemność
        later = [o for o in pool if o.due_at != cutoff]
        n_workers = max(
            1, min(len(free), -(-len(same_due) // max(1, free[0].cart_max_orders * trips_per_worker)))
        )
        crew = free[:n_workers]
        capacity = min(max_orders_per_wave, sum(w.cart_max_orders * trips_per_worker for w in crew))
        chosen = same_due[:capacity]
        if len(chosen) < capacity:
            chosen += later[: capacity - len(chosen)]
        chosen_ids = {o.order_id for o in chosen}
        pool = [o for o in pool if o.order_id not in chosen_ids]
        free = free[n_workers:]
        zones = sorted({z for o in chosen for z in o.zones})
        express = sum(1 for o in chosen if o.priority == "express")
        minutes_left = int((cutoff - now).total_seconds() // 60)
        late = sum(1 for o in chosen if o.order_id in overdue_ids)
        tags = []
        if late:
            tags.append("zaległe")
        if minutes_left < 90:
            tags.append("pilne")
        if express:
            tags.append("ekspresy")
        suggestions.append(
            WaveSuggestion(
                order_ids=[o.order_id for o in chosen],
                worker_ids=[w.worker_id for w in crew],
                cutoff=cutoff,
                zones=zones,
                lines=sum(o.lines for o in chosen),
                volume_l=round(sum(o.volume_l for o in chosen), 1),
                tags=tags,
                rationale=(
                    f"{len(chosen)} zamówień z terminem {cutoff:%H:%M} (zostało {minutes_left} min), "
                    f"{express} ekspresowych, {late} zaległych (po terminie), strefy {','.join(zones) or '-'}; "
                    f"{len(crew)} pracowników × {trips_per_worker} kursy."
                ),
            )
        )
    return suggestions
