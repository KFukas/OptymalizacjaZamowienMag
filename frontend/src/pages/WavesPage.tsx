import { useQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { Badge, Section, StatusBadge, UntrustedText } from "../components/ui";
import { api } from "../lib/api";
import { hhmm, minutes, num, pct } from "../lib/format";
import type { Wave } from "../lib/types";

const STATUSES = ["", "validated", "released", "in_progress", "completed", "discarded"] as const;
const STATUS_FILL: Record<string, string> = {
  validated: "var(--series-4)",
  released: "var(--series-1)",
  in_progress: "var(--series-3)",
  completed: "var(--series-6)",
  discarded: "var(--text-muted)",
};

function Gantt({ waves, simNow }: { waves: Wave[]; simNow: string }) {
  const rows = waves.filter((w) => w.status !== "discarded").slice(0, 14);
  if (!rows.length) return <p className="text-sm text-ink2">Brak fal do pokazania.</p>;
  const t = (iso: string) => new Date(iso).getTime();
  const starts = rows.map((w) => t(w.released_at ?? w.created_at));
  const ends = rows.map((w, i) => Math.max(t(w.cutoff_at), starts[i] + w.est_duration_s * 1000, w.completed_at ? t(w.completed_at) : 0));
  const now = t(simNow);
  const t0 = Math.min(...starts, now);
  const t1 = Math.max(...ends, now) + 5 * 60_000;
  const W = 1400;
  const L = 120;
  const x = (ms: number) => L + ((ms - t0) / (t1 - t0)) * (W - L - 10);
  const rowH = 26;
  const ticks: number[] = [];
  for (let m = Math.ceil(t0 / 1_800_000) * 1_800_000; m < t1; m += 1_800_000) ticks.push(m);
  return (
    <svg viewBox={`0 0 ${W} ${rows.length * rowH + 30}`} className="w-full" role="img" aria-label="Harmonogram fal">
      {ticks.map((m) => (
        <g key={m}>
          <line x1={x(m)} x2={x(m)} y1={0} y2={rows.length * rowH} stroke="var(--gridline)" />
          <text x={x(m)} y={rows.length * rowH + 16} fontSize={11} textAnchor="middle" fill="var(--text-muted)" className="tabular">
            {hhmm(new Date(m).toISOString())}
          </text>
        </g>
      ))}
      {rows.map((w, i) => {
        const s = starts[i];
        const est = s + w.est_duration_s * 1000;
        const late = est > t(w.cutoff_at);
        return (
          <g key={w.wave_id} transform={`translate(0, ${i * rowH})`}>
            <text x={0} y={17} fontSize={12} fill="var(--text-primary)">
              {w.wave_id}
            </text>
            <rect x={x(s)} y={6} width={Math.max(2, x(est) - x(s))} height={14} rx={4} fill={STATUS_FILL[w.status]} opacity={0.35} />
            <rect
              x={x(s)}
              y={6}
              width={Math.max(2, (x(est) - x(s)) * w.progress)}
              height={14}
              rx={4}
              fill={STATUS_FILL[w.status]}
            />
            <line x1={x(t(w.cutoff_at))} x2={x(t(w.cutoff_at))} y1={2} y2={24} stroke="var(--status-critical)" strokeWidth={2} />
            <title>{`${w.wave_id}: start ${hhmm(w.released_at ?? w.created_at)}, szac. koniec ${hhmm(new Date(est).toISOString())}, cut-off ${hhmm(w.cutoff_at)}, postęp ${pct(w.progress)}${late ? " — RYZYKO po cut-off" : ""}`}</title>
          </g>
        );
      })}
      <line x1={x(now)} x2={x(now)} y1={0} y2={rows.length * rowH} stroke="var(--text-primary)" strokeDasharray="3 3" />
    </svg>
  );
}

export function WavesPage() {
  const [status, setStatus] = useState<string>("");
  const [open, setOpen] = useState<string | null>(null);
  const waves = useQuery({
    queryKey: ["waves", status],
    queryFn: () => api<{ sim_now: string; waves: Wave[] }>(`/waves?limit=100${status ? `&status=${status}` : ""}`),
    refetchInterval: 3000,
  });
  const list = waves.data?.waves ?? [];
  return (
    <div className="grid gap-4">
      <Section
        title="Postęp fal"
        actions={
          <label className="text-sm">
            Status:{" "}
            <select value={status} onChange={(e) => setStatus(e.target.value)} className="rounded border bg-transparent px-2 py-1" style={{ borderColor: "var(--border)" }}>
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s || "wszystkie"}
                </option>
              ))}
            </select>
          </label>
        }
      >
        {waves.data ? <Gantt waves={list} simNow={waves.data.sim_now} /> : "Ładowanie…"}
        <p className="mt-2 text-xs text-ink2">
          Pasek: szacowany czas realizacji (wypełnienie = postęp linii), czerwona kreska: cut-off, przerywana: teraz (czas symulacji).
        </p>
      </Section>
      <Section title="Fale">
        <div className="overflow-x-auto">
          <table className="w-full text-sm [&_td]:px-2 [&_th]:px-2">
            <thead className="text-left text-xs uppercase text-ink2">
              <tr>
                <th className="py-2">Fala</th>
                <th>Status</th>
                <th className="text-right">Linie</th>
                <th className="text-right">Postęp</th>
                <th>Cut-off</th>
                <th className="text-right">Szac. czas</th>
                <th className="text-right">Dystans</th>
                <th>Pracownicy</th>
                <th />
              </tr>
            </thead>
            <tbody className="tabular">
              {list.map((w) => {
                const estEnd = new Date(w.released_at ?? w.created_at).getTime() + w.est_duration_s * 1000;
                const risk = estEnd > new Date(w.cutoff_at).getTime() && !["completed", "discarded"].includes(w.status);
                return (
                  <Fragment key={w.wave_id}>
                    <tr className="border-t" style={{ borderColor: "var(--gridline)" }}>
                      <td className="py-2 font-medium">{w.wave_id}</td>
                      <td>
                        <StatusBadge status={w.status} />
                      </td>
                      <td className="text-right">
                        {w.picked_lines}/{w.total_lines}
                      </td>
                      <td className="text-right">{pct(w.progress)}</td>
                      <td>
                        {hhmm(w.cutoff_at)} {risk ? <Badge tone="serious">ryzyko</Badge> : null}
                      </td>
                      <td className="text-right">{minutes(w.est_duration_s)}</td>
                      <td className="text-right">{num(w.est_distance_m)} m</td>
                      <td>{w.assignments.map((a) => a.worker_id).join(", ")}</td>
                      <td>
                        <button className="text-sm underline" onClick={() => setOpen(open === w.wave_id ? null : w.wave_id)}>
                          {open === w.wave_id ? "ukryj" : "dlaczego ta fala?"}
                        </button>
                      </td>
                    </tr>
                    {open === w.wave_id ? (
                      <tr>
                        <td colSpan={9} className="pb-3">
                          <div className="text-xs text-ink2">Uzasadnienie agenta ({w.created_by}):</div>
                          <UntrustedText text={w.rationale} />
                          <div className="mt-1 text-xs text-ink2">
                            Zamówienia: {w.assignments.flatMap((a) => a.order_ids).length} · utworzona {hhmm(w.created_at)}
                            {w.completed_at ? ` · zakończona ${hhmm(w.completed_at)}` : ""}
                          </div>
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
          {!list.length ? <p className="py-4 text-sm text-ink2">Brak fal. Uruchom orkiestrator (agent lub heurystyka).</p> : null}
        </div>
      </Section>
    </div>
  );
}
