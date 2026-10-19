import type { ReactNode } from "react";
import { FLAG_INFO, STATUS_LABEL } from "../lib/format";

/**
 * Tekst z danych (notatka klienta, nazwa SKU, komentarz) — renderowany WYŁĄCZNIE jako tekst.
 * React escapuje zawartość; nigdy nie używamy dangerouslySetInnerHTML.
 */
export function UntrustedText({ text, signals }: { text: string | null | undefined; signals?: string[] }) {
  if (!text) return <span className="text-muted">—</span>;
  return (
    <span
      className="block whitespace-pre-wrap break-words rounded-md border border-dashed px-2 py-1 text-sm"
      style={{ borderColor: "var(--axis)", background: "var(--surface-2)" }}
      data-testid="untrusted-text"
    >
      <span className="mb-1 block text-xs font-medium text-ink2">
        Tekst z danych (niezaufany){signals && signals.length > 0 ? " · wykryto sformułowania podobne do poleceń" : ""}
      </span>
      {text}
    </span>
  );
}

type Tone = "good" | "warning" | "serious" | "critical" | "info" | "neutral";

const TONE_COLOR: Record<Tone, string> = {
  good: "var(--status-good)",
  warning: "var(--status-warning)",
  serious: "var(--status-serious)",
  critical: "var(--status-critical)",
  info: "var(--series-1)",
  neutral: "var(--text-muted)",
};
const TONE_ICON: Record<Tone, string> = { good: "✓", warning: "!", serious: "!", critical: "✕", info: "i", neutral: "•" };

/** Znacznik statusu: kolor ZAWSZE z ikoną i etykietą (nigdy sam kolor). */
export function Badge({ tone, children, title }: { tone: Tone; children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium text-ink"
      style={{ borderColor: TONE_COLOR[tone] }}
    >
      <span
        aria-hidden
        className="inline-flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-bold text-white"
        style={{ background: TONE_COLOR[tone] }}
      >
        {TONE_ICON[tone]}
      </span>
      {children}
    </span>
  );
}

export function statusTone(status: string): Tone {
  if (["executed", "completed", "applied"].includes(status)) return "good";
  if (["pending", "validated", "proposed"].includes(status)) return "warning";
  if (["failed", "expired"].includes(status)) return "serious";
  if (["rejected", "discarded"].includes(status)) return "neutral";
  return "info";
}

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={statusTone(status)}>{STATUS_LABEL[status] ?? status}</Badge>;
}

export function FlagBadge({ flag }: { flag: string }) {
  const info = FLAG_INFO[flag] ?? { label: flag, tone: "info" as const };
  return <Badge tone={info.tone}>{info.label}</Badge>;
}

export function StatTile({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: Tone }) {
  return (
    <div className="card p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-ink2">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-ink">{value}</div>
      {hint ? (
        <div className="mt-1 text-xs text-ink2">
          {tone ? <Badge tone={tone}>{hint}</Badge> : hint}
        </div>
      ) : null}
    </div>
  );
}

export function Section({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="card p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold text-ink">{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function Button({
  children,
  onClick,
  variant = "secondary",
  disabled,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "secondary" | "danger";
  disabled?: boolean;
  type?: "button" | "submit";
}) {
  const style =
    variant === "primary"
      ? { background: "var(--series-1)", color: "#fff", borderColor: "transparent" }
      : variant === "danger"
        ? { background: "var(--status-critical)", color: "#fff", borderColor: "transparent" }
        : { background: "var(--surface-1)", color: "var(--text-primary)", borderColor: "var(--border)" };
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className="rounded-md border px-3 py-1.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50"
      style={style}
    >
      {children}
    </button>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <div role="alert" className="mt-2">
      <Badge tone="critical">{error}</Badge>
    </div>
  );
}
