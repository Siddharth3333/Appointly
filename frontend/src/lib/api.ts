let csrf = "";
export const setCsrf = (t: string) => (csrf = t);
export const toast = (m: string) => window.dispatchEvent(new CustomEvent("toast", { detail: m }));
export async function api<T = any>(path: string, method = "GET", body?: unknown): Promise<T> {
  const r = await fetch("/api" + path, { method, headers: { ...(body !== undefined ? { "Content-Type": "application/json" } : {}), "X-CSRF-Token": csrf },
    body: body !== undefined ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(j.error || "Request failed"), { status: r.status });
  return j;
}
export const fmt = (iso: string, tz?: string) => new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short", timeZone: tz });
