// Клиент кабинета: свой токен (cab_token), свой префикс /api/cab, на 401 — на вход кабинета.
import { qs } from "../api";

const KEY = "cab_token";
export const cabToken = () => localStorage.getItem(KEY) || "";
export const setCabToken = (t: string) => localStorage.setItem(KEY, t);
export const clearCabToken = () => localStorage.removeItem(KEY);
export { qs };

type Opts = { method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE"; body?: unknown; raw?: boolean };

export async function cabApi<T = any>(path: string, opts: Opts = {}): Promise<T> {
  const token = cabToken();
  const res = await fetch(`/api/cab${path}`, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401 && !path.startsWith("/auth/")) {
    clearCabToken();
    if (!location.pathname.startsWith("/cabinet/login")) location.assign("/cabinet/login");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j); } catch {}
    throw new Error(detail);
  }
  if (opts.raw) return res as unknown as T;
  if (res.status === 204) return undefined as T;
  return res.json();
}

export const WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];
export const SUPPLIER_LABEL: Record<string, string> = {
  B222: "Билайн · исходящие", B223: "Билайн · входящие", B333: "МТС · входящие", B111: "исход по сайтам", B221: "Билайн · сайты",
};
