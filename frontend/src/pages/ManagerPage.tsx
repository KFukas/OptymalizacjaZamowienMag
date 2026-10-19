import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Badge, Button, ErrorNote, FlagBadge, Section, StatusBadge, UntrustedText } from "../components/ui";
import { api, explainError } from "../lib/api";
import { dateTime, KIND_LABEL, num } from "../lib/format";
import type { ApprovalRequest, ReslottingProposal } from "../lib/types";

const REASON_LABEL: Record<string, string> = {
  customer_request: "prośba klienta",
  suspected_fraud: "podejrzenie nadużycia",
  duplicate_order: "duplikat zamówienia",
  address_invalid: "błędny adres",
  unfulfillable_stock: "brak towaru nie do uzupełnienia",
  cycle_count: "liczenie cykliczne",
  damage: "uszkodzenie",
  stockout_event: "brak przy kompletacji",
  found_stock: "znaleziony towar",
  system_error: "błąd systemu",
};

function Diff({ before, after }: { before: Record<string, unknown>; after: Record<string, unknown> }) {
  const keys = Array.from(new Set([...Object.keys(before), ...Object.keys(after)]));
  return (
    <table className="w-full text-sm tabular">
      <thead className="text-left text-xs text-ink2">
        <tr>
          <th>Pole</th>
          <th>Przed</th>
          <th>Po decyzji</th>
        </tr>
      </thead>
      <tbody>
        {keys.map((k) => {
          const b = JSON.stringify(before[k] ?? null);
          const a = JSON.stringify(after[k] ?? before[k] ?? null);
          return (
            <tr key={k}>
              <td className="pr-2 text-ink2">{k}</td>
              <td className="pr-2">{b}</td>
              <td className={a !== b ? "font-semibold" : ""}>{a}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function DecisionDialog({ req, onClose }: { req: ApprovalRequest; onClose: () => void }) {
  const qc = useQueryClient();
  const [decision, setDecision] = useState<"approve" | "reject">("reject");
  const [comment, setComment] = useState("");
  const [pin, setPin] = useState("");
  const [confirm, setConfirm] = useState(false);
  const m = useMutation({
    mutationFn: () =>
      api<ApprovalRequest>(`/approvals/${req.request_id}/decide`, {
        method: "POST",
        json: { decision, comment, pin, expected_version: req.version },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["approvals"] });
      onClose();
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["approval", req.request_id] }),
  });
  const ready = comment.trim().length >= 5 && pin.length >= 4 && confirm;
  return (
    <div role="dialog" aria-modal="true" aria-label="Decyzja kierownika" className="fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-4">
      <div className="card w-full max-w-md p-5">
        <h3 className="text-lg font-semibold">
          {KIND_LABEL[req.kind]} · {req.request_id}
        </h3>
        <div className="mt-3 flex gap-4 text-sm">
          <label className="flex items-center gap-2">
            <input type="radio" checked={decision === "approve"} onChange={() => setDecision("approve")} /> Zatwierdź
          </label>
          <label className="flex items-center gap-2">
            <input type="radio" checked={decision === "reject"} onChange={() => setDecision("reject")} /> Odrzuć
          </label>
        </div>
        <label className="mt-3 block text-sm">
          Komentarz (wymagany)
          <textarea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            maxLength={500}
            rows={3}
            className="mt-1 w-full rounded border bg-transparent p-2"
            style={{ borderColor: "var(--border)" }}
          />
        </label>
        <label className="mt-2 block text-sm">
          PIN (ponowne potwierdzenie tożsamości)
          <input
            type="password"
            inputMode="numeric"
            autoComplete="one-time-code"
            value={pin}
            onChange={(e) => setPin(e.target.value)}
            maxLength={12}
            className="mt-1 w-full rounded border bg-transparent p-2"
            style={{ borderColor: "var(--border)" }}
          />
        </label>
        <label className="mt-3 flex items-start gap-2 text-sm">
          <input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} />
          <span>
            {decision === "approve"
              ? "Rozumiem, że zatwierdzenie wykona operację nieodwracalną (jeśli stan się nie zmienił)."
              : "Potwierdzam odrzucenie wniosku."}
          </span>
        </label>
        <ErrorNote error={m.isError ? explainError(m.error) : null} />
        <div className="mt-4 flex justify-end gap-2">
          <Button onClick={onClose}>Anuluj</Button>
          <Button variant={decision === "approve" ? "danger" : "primary"} disabled={!ready || m.isPending} onClick={() => m.mutate()}>
            {decision === "approve" ? "Zatwierdź i wykonaj" : "Odrzuć wniosek"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function RequestDetail({ id }: { id: string }) {
  const q = useQuery({ queryKey: ["approval", id], queryFn: () => api<ApprovalRequest>(`/approvals/${id}`) });
  const [deciding, setDeciding] = useState(false);
  const r = q.data;
  if (!r) return <p className="text-sm text-ink2">Ładowanie…</p>;
  const ctx = r.source_context ?? {};
  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-base font-semibold">{KIND_LABEL[r.kind]}</span>
        <StatusBadge status={r.status} />
        {r.target_changed ? <Badge tone="serious">Stan celu zmienił się od złożenia wniosku</Badge> : null}
      </div>
      <dl className="grid grid-cols-[140px_1fr] gap-x-3 gap-y-1">
        <dt className="text-ink2">Wnioskujący</dt>
        <dd>
          {r.requested_by} ({r.requested_via === "mcp" ? "agent przez MCP" : "panel"})
        </dd>
        <dt className="text-ink2">Cel</dt>
        <dd>
          {r.target_type === "order" ? `zamówienie ${r.target_id}` : `${r.payload.location_id} / ${r.payload.sku_id}`}
        </dd>
        <dt className="text-ink2">Powód</dt>
        <dd>{REASON_LABEL[r.reason_code] ?? r.reason_code}</dd>
        <dt className="text-ink2">Złożony / wygasa</dt>
        <dd>
          {dateTime(r.created_at)} / {dateTime(r.expires_at)}
        </dd>
      </dl>
      {r.flags.length ? (
        <div>
          <div className="mb-1 text-xs font-medium text-ink2">Flagi bezpieczeństwa</div>
          <div className="flex flex-wrap gap-2">
            {r.flags.map((f) => (
              <FlagBadge key={f} flag={f} />
            ))}
          </div>
        </div>
      ) : null}
      <div>
        <div className="mb-1 text-xs font-medium text-ink2">Uzasadnienie wnioskującego</div>
        <UntrustedText text={r.justification} />
      </div>
      {ctx.customer_note !== undefined ? (
        <div>
          <div className="mb-1 text-xs font-medium text-ink2">Notatka klienta {ctx.customer_ref} (dane źródłowe)</div>
          <UntrustedText text={ctx.customer_note} signals={ctx.note_signals} />
        </div>
      ) : null}
      {ctx.sku_name ? (
        <div>
          <div className="mb-1 text-xs font-medium text-ink2">Nazwa SKU (dane źródłowe)</div>
          <UntrustedText text={ctx.sku_name} />
        </div>
      ) : null}
      {ctx.evidence ? (
        <div>
          <div className="mb-1 text-xs font-medium text-ink2">Dowód</div>
          <Badge tone="info">{`${ctx.evidence.ref} · ${ctx.evidence.kind} · ${JSON.stringify(ctx.evidence.details)}`}</Badge>
        </div>
      ) : null}
      <div>
        <div className="mb-1 text-xs font-medium text-ink2">Stan przed / po</div>
        <Diff before={r.before_state} after={r.after_state} />
      </div>
      {r.decision_comment ? (
        <p className="text-ink2">
          Decyzja: {r.decided_by} — „{r.decision_comment}” {r.failure_reason ? `(${r.failure_reason})` : ""}
        </p>
      ) : null}
      {r.status === "pending" ? (
        <Button variant="primary" onClick={() => setDeciding(true)}>
          Podejmij decyzję…
        </Button>
      ) : null}
      {deciding ? <DecisionDialog req={r} onClose={() => setDeciding(false)} /> : null}
    </div>
  );
}

function ReslottingQueue() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["reslotting", "proposed"],
    queryFn: () => api<{ proposals: ReslottingProposal[] }>("/slotting/proposals?status=proposed"),
    refetchInterval: 10_000,
  });
  const m = useMutation({
    mutationFn: (v: { p: ReslottingProposal; decision: "apply" | "reject" }) =>
      api(`/slotting/proposals/${v.p.proposal_id}/decide`, {
        method: "POST",
        json: { decision: v.decision, comment: v.decision === "apply" ? "Akceptuję przesunięcia." : "Odrzucam propozycję.", expected_version: v.p.version },
      }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["reslotting"] }),
  });
  const list = q.data?.proposals ?? [];
  return (
    <Section title="Propozycje przesunięć (slotting)">
      {!list.length ? <p className="text-sm text-ink2">Brak propozycji.</p> : null}
      <ul className="space-y-3">
        {list.map((p) => (
          <li key={p.proposal_id} className="rounded-md border p-3 text-sm" style={{ borderColor: "var(--border)" }}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{p.proposal_id}</span>
              <span className="text-ink2">{p.created_by}</span>
              <Badge tone="good">{`+${num(p.est_gain_m_per_day)} m/dzień`}</Badge>
            </div>
            <UntrustedText text={p.rationale} />
            <ul className="mt-1 text-xs tabular">
              {p.moves.map((mv) => (
                <li key={mv.sku_id + mv.from_location}>
                  {mv.sku_id}: {mv.from_location} → {mv.to_location} ({mv.qty} szt., +{num(mv.est_gain_m_per_day)} m/dzień)
                </li>
              ))}
            </ul>
            <div className="mt-2 flex gap-2">
              <Button variant="primary" disabled={m.isPending} onClick={() => m.mutate({ p, decision: "apply" })}>
                Wykonaj przesunięcia
              </Button>
              <Button disabled={m.isPending} onClick={() => m.mutate({ p, decision: "reject" })}>
                Odrzuć
              </Button>
            </div>
          </li>
        ))}
      </ul>
      <ErrorNote error={m.isError ? explainError(m.error) : null} />
    </Section>
  );
}

export function ManagerPage() {
  const [status, setStatus] = useState("pending");
  const [selected, setSelected] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ["approvals", status],
    queryFn: () => api<{ requests: ApprovalRequest[] }>(`/approvals?limit=200${status ? `&status=${status}` : ""}`),
    refetchInterval: 5000,
  });
  const list = q.data?.requests ?? [];
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(320px,420px)_1fr]">
      <Section
        title="Kolejka wniosków"
        actions={
          <select value={status} onChange={(e) => setStatus(e.target.value)} className="rounded border bg-transparent px-2 py-1 text-sm" style={{ borderColor: "var(--border)" }}>
            <option value="pending">oczekujące</option>
            <option value="">wszystkie</option>
            <option value="executed">wykonane</option>
            <option value="rejected">odrzucone</option>
            <option value="failed">nie wykonano</option>
            <option value="expired">wygasłe</option>
          </select>
        }
      >
        {q.isError ? <ErrorNote error={explainError(q.error)} /> : null}
        <ul className="divide-y" style={{ borderColor: "var(--gridline)" }}>
          {list.map((r) => (
            <li key={r.request_id}>
              <button
                onClick={() => setSelected(r.request_id)}
                className="w-full py-2 text-left text-sm"
                style={{ background: selected === r.request_id ? "var(--surface-2)" : "transparent" }}
                aria-current={selected === r.request_id}
              >
                <div className="flex flex-wrap items-center gap-2 px-1">
                  <span className="font-medium">{r.request_id}</span>
                  <span>{KIND_LABEL[r.kind]}</span>
                  <StatusBadge status={r.status} />
                  {r.flags.some((f) => f.startsWith("source_") || f.startsWith("justification_")) ? <Badge tone="critical">podejrzane źródło</Badge> : null}
                </div>
                <div className="px-1 text-xs text-ink2">
                  {r.target_id} · {r.requested_by} · {dateTime(r.created_at)}
                </div>
              </button>
            </li>
          ))}
        </ul>
        {!list.length ? <p className="text-sm text-ink2">Brak wniosków.</p> : null}
      </Section>
      <div className="grid gap-4">
        <Section title="Szczegóły wniosku">{selected ? <RequestDetail id={selected} /> : <p className="text-sm text-ink2">Wybierz wniosek z kolejki.</p>}</Section>
        <ReslottingQueue />
      </div>
    </div>
  );
}
