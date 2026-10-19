/** Klient REST WMS. Token JWT trzymany w sessionStorage (znika po zamknięciu karty). */

export const API_URL: string = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";

const TOKEN_KEY = "alfa.token";

export function getToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* tryb prywatny — token tylko w pamięci strony */
  }
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public error: string,
    public reason: string,
    public details?: unknown,
  ) {
    super(`${error}: ${reason}`);
  }
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string> | undefined) };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let body = init.body;
  if (init.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  }
  const res = await fetch(`${API_URL}${path}`, { ...init, headers, body });
  const text = await res.text();
  const data = text ? (JSON.parse(text) as Record<string, unknown>) : {};
  if (!res.ok) {
    if (res.status === 401) setToken(null);
    throw new ApiError(
      res.status,
      String(data.error ?? "HTTP_ERROR"),
      String(data.reason ?? res.statusText),
      data.details ?? data.fields,
    );
  }
  return data as T;
}

/** Komunikaty błędów po polsku (kody z WMS). */
export function explainError(e: unknown): string {
  if (!(e instanceof ApiError)) return "Błąd połączenia z WMS.";
  const map: Record<string, string> = {
    reconfirmation_failed: "Niepoprawny PIN.",
    reconfirmation_locked: "Ponowne potwierdzenie zablokowane po zbyt wielu próbach.",
    separation_of_duties: "Nie można decydować o własnym wniosku.",
    not_pending: "Wniosek nie oczekuje już na decyzję.",
    version_mismatch: "Wniosek zmienił się w międzyczasie — odśwież widok.",
    request_expired: "Wniosek wygasł.",
    stale_state: "Stan celu zmienił się od złożenia wniosku — operacja nie została wykonana.",
    invalid_credentials: "Niepoprawny login lub hasło.",
    manager_role_required: "Wymagana rola kierownika zmiany.",
  };
  return map[e.reason] ?? `${e.error}: ${e.reason}`;
}
