import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { SimControls, UserBox, type Theme } from "./components/Header";
import { Button, ErrorNote } from "./components/ui";
import { api, explainError, getToken, setToken } from "./lib/api";
import type { Me } from "./lib/types";
import { AuditPage } from "./pages/AuditPage";
import { BottlenecksPage } from "./pages/BottlenecksPage";
import { ManagerPage } from "./pages/ManagerPage";
import { MapPage } from "./pages/MapPage";
import { WavesPage } from "./pages/WavesPage";

type Tab = "map" | "waves" | "kpi" | "manager" | "audit";

function readPref<T extends string>(key: string, fallback: T): T {
  try {
    return (localStorage.getItem(key) as T | null) ?? fallback;
  } catch {
    return fallback;
  }
}
function writePref(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* brak dostępu do localStorage — preferencja tylko na czas sesji */
  }
}

function Login({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const r = await api<{ access_token: string }>("/auth/login", { method: "POST", json: { username, password } });
      setToken(r.access_token);
      onLogin();
    } catch (err) {
      setError(explainError(err));
    }
  }
  const input = "mt-1 w-full rounded border bg-transparent p-2";
  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <form onSubmit={submit} className="card w-full max-w-sm p-6">
        <h1 className="text-xl font-semibold">Magazyn Centralny Alfa</h1>
        <p className="mt-1 text-sm text-ink2">Optymalizacja kompletacji · dane syntetyczne</p>
        <label className="mt-4 block text-sm">
          Login
          <input className={input} style={{ borderColor: "var(--border)" }} value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
        </label>
        <label className="mt-3 block text-sm">
          Hasło
          <input
            className={input}
            style={{ borderColor: "var(--border)" }}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </label>
        <ErrorNote error={error} />
        <div className="mt-4">
          <Button type="submit" variant="primary">
            Zaloguj
          </Button>
        </div>
        <p className="mt-4 text-xs text-ink2">Konta demo: zob. docs/INSTRUKCJA_URUCHOMIENIA.md (dane w seedzie).</p>
      </form>
    </main>
  );
}

export default function App() {
  const qc = useQueryClient();
  const [authed, setAuthed] = useState(Boolean(getToken()));
  const [tab, setTab] = useState<Tab>(() => readPref<Tab>("alfa.tab", "map"));
  const [theme, setTheme] = useState<Theme>(() =>
    readPref<Theme>("alfa.theme", window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light"),
  );
  const [live, setLive] = useState(false);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    writePref("alfa.theme", theme);
  }, [theme]);
  useEffect(() => writePref("alfa.tab", tab), [tab]);

  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/auth/me"), enabled: authed, retry: false });
  useEffect(() => {
    if (me.isError) setAuthed(false);
  }, [me.isError]);

  if (!authed) return <Login onLogin={() => { setAuthed(true); qc.invalidateQueries(); }} />;
  if (!me.data) return <main className="p-6 text-sm">Ładowanie…</main>;

  const isManager = me.data.role === "kierownik_zmiany";
  const tabs: { id: Tab; label: string; manager?: boolean }[] = [
    { id: "map", label: "Mapa" },
    { id: "waves", label: "Fale" },
    { id: "kpi", label: "Wąskie gardła" },
    { id: "manager", label: "Panel kierownika", manager: true },
    { id: "audit", label: "Audyt", manager: true },
  ];
  const current = !isManager && (tab === "manager" || tab === "audit") ? "map" : tab;

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b px-4 py-2" style={{ background: "var(--surface-1)", borderColor: "var(--border)" }}>
        <div className="mx-auto flex max-w-[1600px] flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <img src="/favicon.svg" alt="" className="h-7 w-7" />
            <span className="font-semibold">Magazyn Centralny Alfa</span>
          </div>
          <SimControls onLive={setLive} />
          <UserBox
            me={me.data}
            theme={theme}
            onTheme={() => setTheme(theme === "dark" ? "light" : "dark")}
            onLogout={() => {
              setToken(null);
              setAuthed(false);
              qc.clear();
            }}
          />
        </div>
        <nav className="mx-auto mt-2 flex max-w-[1600px] flex-wrap gap-1" aria-label="Widoki">
          {tabs
            .filter((t) => !t.manager || isManager)
            .map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                aria-current={current === t.id ? "page" : undefined}
                className="rounded-md px-3 py-1.5 text-sm font-medium"
                style={{ background: current === t.id ? "var(--surface-2)" : "transparent" }}
              >
                {t.label}
              </button>
            ))}
        </nav>
      </header>
      <main className="mx-auto max-w-[1600px] p-4">
        {current === "map" && <MapPage live={live} />}
        {current === "waves" && <WavesPage />}
        {current === "kpi" && <BottlenecksPage />}
        {current === "manager" && isManager && <ManagerPage />}
        {current === "audit" && isManager && <AuditPage />}
      </main>
    </div>
  );
}
