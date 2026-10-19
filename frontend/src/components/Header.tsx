import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, explainError } from "../lib/api";
import { dateTime, SCENARIOS } from "../lib/format";
import type { Me, SimState } from "../lib/types";
import { Badge, Button } from "./ui";

export type Theme = "light" | "dark";

export function SimControls({ onLive }: { onLive: (running: boolean) => void }) {
  const qc = useQueryClient();
  const [scenario, setScenario] = useState("zwykly_dzien");
  const state = useQuery({
    queryKey: ["sim-state"],
    queryFn: async () => {
      const s = await api<SimState>("/sim/state");
      onLive(s.running);
      return s;
    },
    refetchInterval: 1000,
  });
  const control = useMutation({
    mutationFn: (body: Record<string, unknown>) => api("/sim/control", { method: "POST", json: body }),
    onSettled: () => qc.invalidateQueries(),
  });
  const s = state.data;
  const sel = "rounded border bg-transparent px-2 py-1 text-sm";
  return (
    <div className="flex flex-wrap items-center gap-2" aria-label="Sterowanie symulacją">
      <span className="text-sm tabular" aria-live="polite">
        {s ? dateTime(s.sim_now) : "—"}
      </span>
      {s ? <Badge tone={s.running ? "good" : "neutral"}>{s.running ? `działa ×${s.speed}` : "pauza"}</Badge> : null}
      <Button onClick={() => control.mutate({ action: s?.running ? "pause" : "start" })}>{s?.running ? "Pauza" : "Start"}</Button>
      <select
        aria-label="Tempo"
        className={sel}
        style={{ borderColor: "var(--border)" }}
        value={s?.speed ?? 10}
        onChange={(e) => control.mutate({ action: "speed", speed: Number(e.target.value) })}
      >
        {[1, 10, 60, 300].map((v) => (
          <option key={v} value={v}>
            ×{v}
          </option>
        ))}
      </select>
      <Button onClick={() => control.mutate({ action: "step", minutes: 15 })}>+15 min</Button>
      <select aria-label="Scenariusz" className={sel} style={{ borderColor: "var(--border)" }} value={scenario} onChange={(e) => setScenario(e.target.value)}>
        {Object.entries(SCENARIOS).map(([k, v]) => (
          <option key={k} value={k}>
            {v}
          </option>
        ))}
      </select>
      <Button
        onClick={() => {
          if (confirm(`Zresetować symulację do scenariusza „${SCENARIOS[scenario]}”? Audyt i wnioski zostaną zachowane.`))
            control.mutate({ action: "reset", scenario });
        }}
      >
        Reset
      </Button>
      {s ? <span className="text-xs text-ink2">aktualny: {SCENARIOS[s.scenario] ?? s.scenario}</span> : null}
      {control.isError ? <Badge tone="critical">{explainError(control.error)}</Badge> : null}
    </div>
  );
}

export function UserBox({ me, theme, onTheme, onLogout }: { me: Me; theme: Theme; onTheme: () => void; onLogout: () => void }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      <span>
        {me.display_name} <span className="text-ink2">({me.role === "kierownik_zmiany" ? "kierownik zmiany" : me.role})</span>
      </span>
      <Button onClick={onTheme}>{theme === "dark" ? "☀ Jasny" : "☾ Ciemny"}</Button>
      <Button onClick={onLogout}>Wyloguj</Button>
    </div>
  );
}
