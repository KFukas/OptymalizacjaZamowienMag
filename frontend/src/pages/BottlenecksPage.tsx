import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Section, StatTile } from "../components/ui";
import { api } from "../lib/api";
import { dateTime, hhmm, num, pct } from "../lib/format";
import type { AgentReport, Kpis, TimePoint } from "../lib/types";

interface RecentRoute {
  wave_id: string;
  worker_id: string;
  algorithm: string;
  distance_m: number;
  baseline_distance_m: number;
  status: string;
  stops: number;
}

const axisTick = { fill: "var(--text-muted)", fontSize: 11 };
const tooltipStyle = {
  contentStyle: { background: "var(--surface-1)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--text-primary)" },
  labelStyle: { color: "var(--text-secondary)" },
};

export function BottlenecksPage() {
  const [window, setWindow] = useState<"15m" | "1h" | "shift">("1h");
  const kpis = useQuery({ queryKey: ["kpis", window], queryFn: () => api<Kpis>(`/kpis?window=${window}`), refetchInterval: 3000 });
  const ts = useQuery({
    queryKey: ["kpi-ts"],
    queryFn: () => api<{ points: TimePoint[] }>("/kpis/timeseries?bucket_min=15&hours=8"),
    refetchInterval: 10_000,
  });
  const routes = useQuery({ queryKey: ["routes-recent"], queryFn: () => api<{ routes: RecentRoute[] }>("/routes/recent?limit=30"), refetchInterval: 10_000 });
  const reports = useQuery({ queryKey: ["reports"], queryFn: () => api<{ reports: AgentReport[] }>("/reports?limit=10"), refetchInterval: 15_000 });
  const k = kpis.data;

  const routeData = [...(routes.data?.routes ?? [])].reverse().map((r) => ({
    name: `${r.wave_id.slice(5)}/${r.worker_id.slice(4)}`,
    agent: r.distance_m,
    sshape: r.baseline_distance_m,
    algorithm: r.algorithm,
  }));
  const totAgent = routeData.reduce((a, r) => a + r.agent, 0);
  const totBase = routeData.reduce((a, r) => a + r.sshape, 0);
  const gain = totBase > 0 ? (totBase - totAgent) / totBase : null;
  const aisleData = Object.entries(k?.picks_by_aisle ?? {}).map(([a, v]) => ({ aisle: a, picks: v }));
  const tsData = (ts.data?.points ?? []).map((p) => ({ ...p, label: hhmm(p.t) }));

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-ink2">Okno KPI:</span>
        {(["15m", "1h", "shift"] as const).map((w) => (
          <button
            key={w}
            onClick={() => setWindow(w)}
            className="rounded-md border px-2 py-1"
            style={{ borderColor: "var(--border)", background: window === w ? "var(--surface-2)" : "transparent" }}
            aria-pressed={window === w}
          >
            {w === "15m" ? "15 min" : w === "1h" ? "1 h" : "zmiana"}
          </button>
        ))}
        <span className="ml-auto text-ink2">Czas symulacji: {k ? dateTime(k.sim_now) : "—"}</span>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        <StatTile label="Linie / h" value={num(k?.lines_per_hour)} hint={`${num(k?.lines_per_hour_per_worker)} na osobę`} />
        <StatTile label="OTIF" value={pct(k?.otif, 1)} hint={`${num(k?.shipped)} wysłanych`} />
        <StatTile label="Utylizacja" value={pct(k?.utilization)} hint={`${k?.workers_busy ?? 0}/${k?.workers_on_shift ?? 0} w pracy`} />
        <StatTile
          label="Kolejka pakowania"
          value={num(k?.pack_queue)}
          hint={(k?.pack_queue ?? 0) >= 6 ? "wąskie gardło" : "w normie"}
          tone={(k?.pack_queue ?? 0) >= 6 ? "critical" : "good"}
        />
        <StatTile label="Otwarte zam." value={num(k?.open_orders)} />
        <StatTile label="Zagrożone (<2 h)" value={num(k?.orders_at_risk)} tone={(k?.orders_at_risk ?? 0) > 0 ? "warning" : undefined} hint={(k?.orders_at_risk ?? 0) > 0 ? "zaplanuj" : undefined} />
        <StatTile label="Braki towaru" value={num(k?.stockouts.length)} />
        <StatTile label="Dystans / zam." value={k?.distance_per_order_m ? `${num(k.distance_per_order_m, 1)} m` : "—"} />
      </div>

      <Section title="Alerty">
        {k?.alerts.length ? (
          <ul className="flex flex-wrap gap-2">
            {k.alerts.map((a, i) => (
              <li key={i}>
                <Badge tone={a.severity === "high" ? "critical" : a.severity === "medium" ? "warning" : "info"}>{a.message}</Badge>
              </li>
            ))}
          </ul>
        ) : (
          <Badge tone="good">Brak alertów</Badge>
        )}
      </Section>

      <div className="grid gap-4 xl:grid-cols-2">
        <Section title="Tempo kompletacji (linie / h, kubełki 15 min)">
          <div className="h-64">
            <ResponsiveContainer>
              <LineChart data={tsData} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="var(--gridline)" vertical={false} />
                <XAxis dataKey="label" tick={axisTick} stroke="var(--axis)" minTickGap={24} />
                <YAxis tick={axisTick} stroke="var(--axis)" width={44} />
                <Tooltip {...tooltipStyle} formatter={(v: number) => num(v)} />
                <Line type="monotone" dataKey="lines_per_hour" name="Linie / h" stroke="var(--series-1)" strokeWidth={2} dot={false} activeDot={{ r: 4 }} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Section>
        <Section title="Pobrania wg alejki (heatmapa rotacji w oknie)">
          <div className="h-64">
            <ResponsiveContainer>
              <BarChart data={aisleData} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="var(--gridline)" vertical={false} />
                <XAxis dataKey="aisle" tick={axisTick} stroke="var(--axis)" />
                <YAxis tick={axisTick} stroke="var(--axis)" width={44} allowDecimals={false} />
                <Tooltip {...tooltipStyle} cursor={{ fill: "var(--surface-2)" }} />
                <Bar dataKey="picks" name="Pobrania" fill="var(--series-1)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Section>
      </div>

      <Section
        title="Agent vs heurystyka bazowa (S-shape) — dystans tras"
        actions={gain !== null ? <Badge tone={gain > 0 ? "good" : "serious"}>{`łącznie ${gain > 0 ? "−" : "+"}${pct(Math.abs(gain), 1)} dystansu`}</Badge> : null}
      >
        <div className="h-72">
          <ResponsiveContainer>
            <BarChart data={routeData} margin={{ top: 8, right: 16, bottom: 0, left: 0 }} barGap={2}>
              <CartesianGrid stroke="var(--gridline)" vertical={false} />
              <XAxis dataKey="name" tick={axisTick} stroke="var(--axis)" interval={0} angle={-35} textAnchor="end" height={50} />
              <YAxis tick={axisTick} stroke="var(--axis)" width={48} unit=" m" />
              <Tooltip {...tooltipStyle} cursor={{ fill: "var(--surface-2)" }} formatter={(v: number) => `${num(v)} m`} />
              <Legend wrapperStyle={{ color: "var(--text-secondary)", fontSize: 12 }} />
              <Bar dataKey="agent" name="Trasa agenta (OR-Tools)" fill="var(--series-1)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
              <Bar dataKey="sshape" name="S-shape (bazowa)" fill="var(--series-2)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <details className="mt-2 text-sm">
          <summary className="cursor-pointer text-ink2">Tabela danych</summary>
          <table className="mt-2 w-full tabular">
            <thead className="text-left text-xs text-ink2">
              <tr>
                <th>Fala/pracownik</th>
                <th>Algorytm</th>
                <th className="text-right">Agent [m]</th>
                <th className="text-right">S-shape [m]</th>
              </tr>
            </thead>
            <tbody>
              {routeData.map((r) => (
                <tr key={r.name}>
                  <td>{r.name}</td>
                  <td>{r.algorithm}</td>
                  <td className="text-right">{num(r.agent, 1)}</td>
                  <td className="text-right">{num(r.sshape, 1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      </Section>

      <Section title="Raporty agenta (uzasadnienia decyzji)">
        <ul className="space-y-3">
          {(reports.data?.reports ?? []).map((r) => (
            <li key={r.id} className="rounded-md border p-3" style={{ borderColor: "var(--border)" }}>
              <div className="flex flex-wrap items-center gap-2 text-xs text-ink2">
                <Badge tone={r.mode === "llm" ? "info" : "neutral"}>{r.mode === "llm" ? "agent LLM" : "heurystyka"}</Badge>
                <span>czas symulacji {dateTime(r.sim_time)}</span>
                <span>· {r.author}</span>
              </div>
              {/* treść raportu pochodzi od agenta — renderujemy jako tekst */}
              <p className="mt-1 whitespace-pre-wrap text-sm">{r.summary}</p>
              {r.decisions.length ? (
                <ul className="mt-1 list-disc pl-5 text-xs text-ink2">
                  {r.decisions.slice(0, 8).map((d, i) => (
                    <li key={i}>
                      <span className="font-medium text-ink">{d.action}</span> {d.target} — {d.why}
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
          {!reports.data?.reports.length ? <li className="text-sm text-ink2">Brak raportów.</li> : null}
        </ul>
      </Section>
    </div>
  );
}
