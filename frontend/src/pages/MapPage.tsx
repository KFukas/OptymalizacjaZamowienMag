import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Section } from "../components/ui";
import { api } from "../lib/api";
import { BLUE_RAMP, ORANGE_RAMP, num, rampColor, seriesVar } from "../lib/format";
import type { Kpis, Layout, ReslottingProposal, Route, WorkerPos } from "../lib/types";

const BAY_DEPTH = 1.4;
const RACK_W = 1.1;

const STATE_STYLE: Record<string, { color: string; label: string }> = {
  walking: { color: "var(--series-1)", label: "idzie" },
  picking: { color: "var(--series-3)", label: "kompletuje" },
  break: { color: "var(--series-4)", label: "przerwa" },
  idle: { color: "var(--text-muted)", label: "bez zadania" },
  packing: { color: "var(--series-7)", label: "pakowanie" },
};

type Heat = "none" | "rotation" | "fill";

export function MapPage({ live }: { live: boolean }) {
  const [heat, setHeat] = useState<Heat>("rotation");
  const [layers, setLayers] = useState({ workers: true, routes: true, moves: true, congestion: true });
  const layout = useQuery({ queryKey: ["layout"], queryFn: () => api<Layout>("/layout"), staleTime: Infinity });
  const heatmap = useQuery({
    queryKey: ["heatmap", heat],
    queryFn: () => api<{ max: number; values: Record<string, number> }>(`/layout/heatmap?metric=${heat === "fill" ? "fill" : "rotation"}`),
    enabled: heat !== "none",
    refetchInterval: 30_000,
  });
  const workers = useQuery({
    queryKey: ["workers-pos"],
    queryFn: () => api<{ workers: WorkerPos[] }>("/sim/workers"),
    refetchInterval: live ? 1000 : 5000,
  });
  const routes = useQuery({
    queryKey: ["routes-active"],
    queryFn: () => api<{ routes: Route[] }>("/routes/active"),
    refetchInterval: live ? 3000 : 10_000,
  });
  const kpis = useQuery({ queryKey: ["kpis", "15m"], queryFn: () => api<Kpis>("/kpis?window=15m"), refetchInterval: 5000 });
  const proposals = useQuery({
    queryKey: ["reslotting", "proposed"],
    queryFn: () => api<{ proposals: ReslottingProposal[] }>("/slotting/proposals?status=proposed"),
    refetchInterval: 15_000,
  });

  const geo = layout.data?.geometry;
  const flipY = (y: number) => (geo ? geo.back_y - y : y);

  // heatmapa: suma po poziomach dla każdego przęsła (aisle, bay)
  const bayHeat = useMemo(() => {
    const out = new Map<string, number>();
    if (!layout.data || !heatmap.data) return { out, max: 0 };
    for (const loc of layout.data.locations) {
      const v = heatmap.data.values[loc.location_id] ?? 0;
      const k = `${loc.aisle}-${loc.bay}`;
      out.set(k, (out.get(k) ?? 0) + v);
    }
    return { out, max: Math.max(0, ...out.values()) };
  }, [layout.data, heatmap.data]);

  const locXY = useMemo(() => {
    const m = new Map<string, { x: number; y: number }>();
    for (const l of layout.data?.locations ?? []) m.set(l.location_id, { x: l.x, y: l.y });
    return m;
  }, [layout.data]);

  if (layout.isLoading || !geo || !layout.data) return <Section title="Mapa magazynu">Ładowanie układu…</Section>;

  const bays = Array.from({ length: geo.bays }, (_, i) => i + 1);
  const congestion = kpis.data?.congestion_by_aisle ?? {};
  const congMax = Math.max(3, ...Object.values(congestion));
  const workerIndex = (id: string) => Number(id.slice(4)) - 1;
  const vb = `-3 -2 ${geo.width + 4} ${geo.back_y + 8}`;

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
      <Section title="Mapa magazynu (widok z góry, pakowanie na dole)">
        <div className="overflow-x-auto">
          <svg viewBox={vb} className="h-auto w-full min-w-[640px]" role="img" aria-label="Mapa magazynu">
            {/* strefy */}
            {layout.data.zones.map((z) => {
              const x0 = geo.aisles.find((a) => a.aisle === z.aisle_from)!.x - geo.aisle_spacing / 2;
              const x1 = geo.aisles.find((a) => a.aisle === z.aisle_to)!.x + geo.aisle_spacing / 2;
              return (
                <g key={z.zone}>
                  <rect
                    x={x0}
                    y={0}
                    width={x1 - x0}
                    height={geo.back_y}
                    fill={`var(--zone-${z.zone.toLowerCase()})`}
                    stroke="var(--gridline)"
                    strokeWidth={0.1}
                  />
                  <text x={(x0 + x1) / 2} y={-0.6} fontSize={1.1} textAnchor="middle" fill="var(--text-secondary)">
                    {z.name}
                  </text>
                </g>
              );
            })}
            {/* alejki: tło zatoru + regały */}
            {geo.aisles.map((a) => {
              const c = congestion[String(a.aisle)] ?? 0;
              return (
                <g key={a.aisle}>
                  {layers.congestion && c > 0 ? (
                    <g>
                      <rect x={a.x - 1.3} y={geo.back_y + 0.25} width={2.6} height={0.75} rx={0.2} fill={rampColor(c, congMax, ORANGE_RAMP)} />
                      <text x={a.x} y={geo.back_y + 0.82} fontSize={0.6} textAnchor="middle" fill="#fff" fontWeight={700}>
                        {c}
                      </text>
                      <title>{`Alejka ${a.aisle}: ${c} pracowników (zator przy 3+)`}</title>
                    </g>
                  ) : null}
                  {bays.map((b) => {
                    const left = b % 2 === 1;
                    const y = geo.rack_start_y + (Math.ceil(b / 2) - 1) * BAY_DEPTH;
                    const v = bayHeat.out.get(`${a.aisle}-${b}`) ?? 0;
                    const fill = heat !== "none" && v > 0 ? rampColor(v, bayHeat.max, BLUE_RAMP) : "var(--surface-1)";
                    return (
                      <rect
                        key={b}
                        x={left ? a.x - 0.45 - RACK_W : a.x + 0.45}
                        y={flipY(y + BAY_DEPTH) + 0.05}
                        width={RACK_W}
                        height={BAY_DEPTH - 0.1}
                        rx={0.15}
                        fill={fill}
                        stroke="var(--rack)"
                        strokeWidth={0.06}
                      >
                        <title>{`Alejka ${a.aisle}, przęsło ${b}${heat !== "none" ? ` — ${heat === "fill" ? "zapełnienie" : "rotacja (szt./dzień)"}: ${num(v, 2)}` : ""}`}</title>
                      </rect>
                    );
                  })}
                  <text x={a.x} y={geo.back_y + 2} fontSize={0.9} textAnchor="middle" fill="var(--text-muted)">
                    {String(a.aisle).padStart(2, "0")}
                  </text>
                </g>
              );
            })}
            {/* punkt pakowania */}
            <g>
              <rect x={geo.depot.x - 1.2} y={flipY(geo.depot.y) + 0.3} width={2.4} height={1.4} rx={0.3} fill="var(--series-7)" />
              <text x={geo.depot.x} y={flipY(geo.depot.y) + 2.9} fontSize={0.9} textAnchor="middle" fill="var(--text-secondary)">
                pakowanie
              </text>
            </g>
            {/* trasy aktywnych fal */}
            {layers.routes &&
              (routes.data?.routes ?? []).map((r) => (
                <polyline
                  key={`${r.wave_id}-${r.worker_id}`}
                  points={r.path.map(([x, y]) => `${x},${flipY(y)}`).join(" ")}
                  fill="none"
                  stroke={seriesVar(workerIndex(r.worker_id))}
                  strokeWidth={0.16}
                  strokeLinejoin="round"
                  className={r.status === "active" ? "route-animated" : undefined}
                  opacity={r.status === "active" ? 0.9 : 0.35}
                >
                  <title>{`${r.wave_id} · ${r.worker_id} · ${r.algorithm} · ${num(r.distance_m)} m (S-shape ${num(r.baseline_distance_m)} m)`}</title>
                </polyline>
              ))}
            {/* propozycje przesunięć */}
            {layers.moves &&
              (proposals.data?.proposals ?? []).flatMap((p) =>
                p.moves.map((m) => {
                  const a = locXY.get(m.from_location);
                  const b = locXY.get(m.to_location);
                  if (!a || !b) return null;
                  return (
                    <g key={`${p.proposal_id}-${m.sku_id}`}>
                      <line
                        x1={a.x}
                        y1={flipY(a.y)}
                        x2={b.x}
                        y2={flipY(b.y)}
                        stroke="var(--series-2)"
                        strokeWidth={0.2}
                        markerEnd="url(#arrow)"
                      />
                      <title>{`${p.proposal_id}: ${m.sku_id} ${m.from_location} → ${m.to_location} (+${num(m.est_gain_m_per_day)} m/dzień)`}</title>
                    </g>
                  );
                }),
              )}
            <defs>
              <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--series-2)" />
              </marker>
            </defs>
            {/* pracownicy */}
            {layers.workers &&
              (workers.data?.workers ?? [])
                .filter((w) => w.active)
                .map((w, i, all) => {
                  const st = STATE_STYLE[w.state] ?? STATE_STYLE.idle;
                  // bezczynni w punkcie pakowania: rozłożeni w rzędzie wzdłuż alejki poprzecznej
                  const atDepot = w.state === "idle" && Math.abs(w.x - geo.depot.x) < 0.5 && Math.abs(w.y - geo.depot.y) < 0.5;
                  const idleIdx = all.slice(0, i).filter((o) => o.state === "idle").length;
                  const x = atDepot ? geo.depot.x + 3 + idleIdx * 1.7 : w.x;
                  const y = atDepot ? flipY(geo.depot.y) + 3.6 : flipY(w.y);
                  return (
                    <g key={w.worker_id} transform={`translate(${x},${y})`}>
                      <circle r={0.75} fill={st.color} stroke="var(--surface-1)" strokeWidth={0.2} />
                      <text y={0.3} fontSize={0.7} textAnchor="middle" fill="#fff" fontWeight={700}>
                        {w.worker_id.slice(5)}
                      </text>
                      <title>{`${w.worker_id}: ${st.label}`}</title>
                    </g>
                  );
                })}
          </svg>
        </div>
      </Section>

      <div className="flex flex-col gap-4">
        <Section title="Warstwy">
          <fieldset className="flex flex-col gap-2 text-sm">
            <legend className="mb-1 text-xs text-ink2">Heatmapa regałów</legend>
            {(["rotation", "fill", "none"] as Heat[]).map((h) => (
              <label key={h} className="flex items-center gap-2">
                <input type="radio" name="heat" checked={heat === h} onChange={() => setHeat(h)} />
                {h === "rotation" ? "Rotacja (popyt 28 dni)" : h === "fill" ? "Zapełnienie lokalizacji" : "Brak"}
              </label>
            ))}
          </fieldset>
          <div className="mt-3 flex flex-col gap-2 text-sm">
            {(
              [
                ["workers", "Pracownicy na żywo"],
                ["routes", "Trasy aktywnych fal"],
                ["moves", "Propozycje przesunięć"],
                ["congestion", "Zatory w alejkach"],
              ] as const
            ).map(([k, label]) => (
              <label key={k} className="flex items-center gap-2">
                <input type="checkbox" checked={layers[k]} onChange={(e) => setLayers({ ...layers, [k]: e.target.checked })} />
                {label}
              </label>
            ))}
          </div>
        </Section>
        <Section title="Legenda">
          <ul className="space-y-1 text-sm">
            {Object.entries(STATE_STYLE).map(([k, v]) => (
              <li key={k} className="flex items-center gap-2">
                <span className="inline-block h-3 w-3 rounded-full" style={{ background: v.color }} /> {v.label}
              </li>
            ))}
            <li className="flex items-center gap-2">
              <span className="inline-block h-0.5 w-5" style={{ background: "var(--series-2)" }} /> przesunięcie (propozycja)
            </li>
          </ul>
          {heat !== "none" ? (
            <div className="mt-3">
              <div className="text-xs text-ink2">{heat === "fill" ? "Zapełnienie" : "Rotacja"}: mało → dużo</div>
              <div className="mt-1 flex h-3 overflow-hidden rounded">
                {BLUE_RAMP.map((c) => (
                  <span key={c} className="flex-1" style={{ background: c }} />
                ))}
              </div>
            </div>
          ) : null}
          <div className="mt-3">
            <div className="text-xs text-ink2">Zator (pasek pod alejką, liczba osób): 1 → {congMax}+</div>
            <div className="mt-1 flex h-3 overflow-hidden rounded">
              {ORANGE_RAMP.map((c) => (
                <span key={c} className="flex-1" style={{ background: c }} />
              ))}
            </div>
          </div>
        </Section>
      </div>
    </div>
  );
}
