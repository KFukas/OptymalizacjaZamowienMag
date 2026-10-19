"""Prognoza popytu: ETS / Holt-Winters (statsmodels) vs baseline naiwny sezonowy, z backtestem (WAPE/MAPE).

Dla każdego SKU:
1. backtest na ostatnich `holdout` dniach: dopasowanie na wcześniejszych danych, prognoza holdoutu,
2. wybór modelu o mniejszym WAPE (ETS, naiwny sezonowy, średnia — dla popytu sporadycznego),
3. prognoza `horizon` dni zwycięskim modelem dopasowanym do całej historii.
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

SEASON = 7
log = logging.getLogger(__name__)
Model = Callable[[np.ndarray, int], np.ndarray]


@dataclass
class ForecastResult:
    sku_id: str
    model: str
    forecast: list[float]
    wape: float | None
    mape: float | None
    baseline_wape: float | None
    mean_daily: float

    def as_dict(self) -> dict:
        return {
            "sku_id": self.sku_id,
            "model": self.model,
            "forecast": [round(v, 2) for v in self.forecast],
            "total": round(sum(self.forecast), 1),
            "wape_backtest": None if self.wape is None else round(self.wape, 3),
            "mape_backtest": None if self.mape is None else round(self.mape, 3),
            "baseline_wape_backtest": None if self.baseline_wape is None else round(self.baseline_wape, 3),
            "mean_daily": round(self.mean_daily, 2),
        }


def wape(actual: np.ndarray, pred: np.ndarray) -> float | None:
    denom = float(np.abs(actual).sum())
    return None if denom == 0 else float(np.abs(actual - pred).sum()) / denom


def mape(actual: np.ndarray, pred: np.ndarray) -> float | None:
    mask = actual > 0
    if not mask.any():
        return None
    return float(np.mean(np.abs(actual[mask] - pred[mask]) / actual[mask]))


def naive_seasonal(history: np.ndarray, horizon: int) -> np.ndarray:
    if len(history) < SEASON:
        return np.full(horizon, float(history.mean()) if len(history) else 0.0)
    last = history[-SEASON:]
    return np.array([last[i % SEASON] for i in range(horizon)], dtype=float)


def mean_forecast(history: np.ndarray, horizon: int, window: int = 28) -> np.ndarray:
    return np.full(horizon, float(history[-window:].mean()) if len(history) else 0.0)


def ets_forecast(history: np.ndarray, horizon: int) -> np.ndarray:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = ExponentialSmoothing(
            history.astype(float),
            trend="add",
            damped_trend=True,
            seasonal="add",
            seasonal_periods=SEASON,
            initialization_method="estimated",
        ).fit(optimized=True)
        out = np.asarray(model.forecast(horizon), dtype=float)
    return np.clip(np.nan_to_num(out, nan=0.0), 0.0, None)


def _is_intermittent(history: np.ndarray) -> bool:
    return bool(len(history) < 4 * SEASON or (history == 0).mean() > 0.5 or history.mean() < 0.7)


def forecast_sku(sku_id: str, history: np.ndarray, horizon: int, holdout: int = 14) -> ForecastResult:
    history = np.asarray(history, dtype=float)
    candidates: dict[str, Model] = {"naive_seasonal": naive_seasonal, "mean_28d": mean_forecast}
    if not _is_intermittent(history):
        candidates["ets_holt_winters"] = ets_forecast
    scores: dict[str, tuple[float | None, float | None]] = {}
    if len(history) > holdout + 2 * SEASON:
        train, test = history[:-holdout], history[-holdout:]
        for name, fn in candidates.items():
            try:
                pred = fn(train, holdout)
            except Exception:  # model może się nie zbiec — pomijamy kandydata
                log.debug("model %s nie zbiegł się dla %s", name, sku_id, exc_info=True)
                continue
            scores[name] = (wape(test, pred), mape(test, pred))

    def key(name: str) -> float:
        w = scores.get(name, (None, None))[0]
        return float("inf") if w is None else w

    best = min(candidates, key=lambda n: (key(n), n != "ets_holt_winters")) if scores else "mean_28d"
    try:
        fc = candidates[best](history, horizon)
    except Exception:  # noqa: BLE001
        best, fc = "mean_28d", mean_forecast(history, horizon)
    w, m = scores.get(best, (None, None))
    return ForecastResult(
        sku_id=sku_id,
        model=best,
        forecast=[float(v) for v in fc],
        wape=w,
        mape=m,
        baseline_wape=scores.get("naive_seasonal", (None, None))[0],
        mean_daily=float(history[-28:].mean()) if len(history) else 0.0,
    )


def aggregate_wape(results: list[ForecastResult]) -> float | None:
    """WAPE ważony wolumenem (średnia WAPE ważona średnim popytem)."""
    num = sum((r.wape or 0.0) * r.mean_daily for r in results if r.wape is not None)
    den = sum(r.mean_daily for r in results if r.wape is not None)
    return None if den == 0 else num / den
