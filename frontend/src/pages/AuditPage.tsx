import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Badge, Button, Section } from "../components/ui";
import { api } from "../lib/api";
import { dateTime } from "../lib/format";
import type { AuditEntry } from "../lib/types";

interface Verify {
  ok: boolean;
  checked: number;
  first_broken_seq: number | null;
  problem: string | null;
}

export function AuditPage() {
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");
  const [decision, setDecision] = useState("");
  const [source, setSource] = useState("");
  const params = new URLSearchParams({ limit: "200" });
  if (actor) params.set("actor", actor);
  if (action) params.set("action", action);
  if (decision) params.set("decision", decision);
  if (source) params.set("source", source);
  const q = useQuery({
    queryKey: ["audit", params.toString()],
    queryFn: () => api<{ entries: AuditEntry[] }>(`/audit?${params.toString()}`),
    refetchInterval: 5000,
  });
  const verify = useMutation({ mutationFn: () => api<Verify>("/audit/verify") });
  const inputCls = "rounded border bg-transparent px-2 py-1 text-sm";
  const border = { borderColor: "var(--border)" };
  return (
    <div className="grid gap-4">
      <Section
        title="Weryfikacja łańcucha hashy"
        actions={
          <Button variant="primary" onClick={() => verify.mutate()} disabled={verify.isPending}>
            Zweryfikuj cały dziennik
          </Button>
        }
      >
        {verify.data ? (
          verify.data.ok ? (
            <Badge tone="good">{`Łańcuch nienaruszony — sprawdzono ${verify.data.checked} wpisów`}</Badge>
          ) : (
            <Badge tone="critical">
              {`Wykryto manipulację: ${verify.data.problem}${verify.data.first_broken_seq ? ` od wpisu #${verify.data.first_broken_seq}` : ""}`}
            </Badge>
          )
        ) : (
          <p className="text-sm text-ink2">
            Każdy wpis zawiera skrót poprzedniego (prev_hash). Zmiana lub usunięcie wpisu przerywa łańcuch.
          </p>
        )}
      </Section>
      <Section title="Dziennik audytu (append-only)">
        <div className="mb-3 flex flex-wrap gap-2">
          <input placeholder="aktor" value={actor} onChange={(e) => setActor(e.target.value)} className={inputCls} style={border} />
          <input placeholder="akcja (prefiks)" value={action} onChange={(e) => setAction(e.target.value)} className={inputCls} style={border} />
          <select value={decision} onChange={(e) => setDecision(e.target.value)} className={inputCls} style={border}>
            <option value="">decyzja: wszystkie</option>
            <option value="allow">allow</option>
            <option value="deny">deny</option>
          </select>
          <select value={source} onChange={(e) => setSource(e.target.value)} className={inputCls} style={border}>
            <option value="">źródło: wszystkie</option>
            <option value="mcp">mcp-wms</option>
            <option value="wms">WMS</option>
          </select>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs tabular">
            <thead className="text-left uppercase text-ink2">
              <tr>
                <th className="py-1">#</th>
                <th>Czas</th>
                <th>Źródło</th>
                <th>Aktor</th>
                <th>Akcja</th>
                <th>Cel</th>
                <th>Decyzja</th>
                <th>Powód</th>
                <th>Hash</th>
              </tr>
            </thead>
            <tbody>
              {(q.data?.entries ?? []).map((e) => (
                <tr key={e.seq} className="border-t align-top" style={{ borderColor: "var(--gridline)" }}>
                  <td className="py-1">{e.seq}</td>
                  <td className="whitespace-nowrap">{dateTime(e.ts)}</td>
                  <td>{e.source}</td>
                  <td className="max-w-[220px] break-all">{e.actor}</td>
                  <td>
                    <details>
                      <summary className="cursor-pointer">{e.action}</summary>
                      <pre className="mt-1 max-w-[420px] whitespace-pre-wrap break-all text-[11px] text-ink2">{JSON.stringify(e.args, null, 1)}</pre>
                    </details>
                  </td>
                  <td>{e.target}</td>
                  <td>{e.decision === "allow" ? <Badge tone="good">allow</Badge> : <Badge tone="critical">deny</Badge>}</td>
                  <td className="max-w-[200px] break-all">{e.reason}</td>
                  <td title={`prev ${e.prev_hash}\nhash ${e.hash}`} className="font-mono">
                    {e.hash.slice(0, 10)}…
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}
