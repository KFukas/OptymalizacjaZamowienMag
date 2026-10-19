export const SERIES = ["--series-1", "--series-2", "--series-3", "--series-4", "--series-5", "--series-6", "--series-7", "--series-8"];

/** Kolor serii po STAŁYM indeksie encji (nie po rangze) — filtr nie przemalowuje pozostałych. */
export function seriesVar(index: number): string {
  return `var(${SERIES[index % SERIES.length]})`;
}

/** Sekwencyjna rampa niebieska (jasny -> ciemny) dla heatmapy wielkości. */
export const BLUE_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];
/** Druga rampa (pomarańczowa, porządkowa) — liczba osób w alejce. Zwalidowana: jasny koniec >= 2:1 w obu trybach. */
export const ORANGE_RAMP = ["#f08a5d", "#eb6834", "#c4501f", "#9a3d16"];

export function rampColor(value: number, max: number, ramp: string[] = BLUE_RAMP): string {
  if (max <= 0 || value <= 0) return "transparent";
  const t = Math.min(1, value / max);
  return ramp[Math.min(ramp.length - 1, Math.floor(t * ramp.length))];
}

export function hhmm(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString("pl-PL", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function pct(v: number | null | undefined, digits = 0): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)}%`;
}

export function num(v: number | null | undefined, digits = 0): string {
  return v === null || v === undefined ? "—" : v.toLocaleString("pl-PL", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function minutes(seconds: number): string {
  return `${Math.round(seconds / 60)} min`;
}

/** Opisy flag wniosków dla kierownika (bez żargonu). */
export const FLAG_INFO: Record<string, { label: string; tone: "warning" | "critical" | "info" | "good" }> = {
  requested_by_agent: { label: "Wniosek złożony przez agenta AI", tone: "info" },
  source_contains_instructions: {
    label: "Dane źródłowe (notatka / nazwa) zawierają treść podobną do poleceń — możliwa manipulacja",
    tone: "critical",
  },
  justification_quotes_untrusted_text: {
    label: "Uzasadnienie cytuje notatkę klienta — wniosek mógł zostać wywołany treścią notatki",
    tone: "critical",
  },
  justification_contains_instructions: { label: "Uzasadnienie zawiera sformułowania podobne do poleceń", tone: "warning" },
  order_flagged_suspicious: { label: "System oznaczył zamówienie jako podejrzane", tone: "warning" },
  large_delta: { label: "Duża różnica stanu (powyżej limitu bez dowodu)", tone: "warning" },
};

export const STATUS_LABEL: Record<string, string> = {
  pending: "oczekuje",
  approved: "zatwierdzony",
  executed: "wykonany",
  rejected: "odrzucony",
  expired: "wygasł",
  failed: "nie wykonano",
  validated: "zaplanowana",
  released: "zwolniona",
  in_progress: "w realizacji",
  completed: "zakończona",
  discarded: "wycofana",
  proposed: "zaproponowana",
  applied: "wykonana",
};

export const KIND_LABEL: Record<string, string> = {
  order_cancellation: "Anulowanie zamówienia",
  inventory_adjustment: "Korekta stanu",
};

export const SCENARIOS: Record<string, string> = {
  zwykly_dzien: "Zwykły dzień",
  black_friday: "Black Friday",
  brak_pracownikow: "Brak 2 pracowników",
  rozbieznosc_stanow: "Rozbieżność stanów",
  zamowienie_podejrzane: "Zamówienie podejrzane",
  notatka_injection: "Notatka z injection",
};
