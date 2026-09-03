import { Badge, Group, Paper, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

export const n = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : Math.round(v).toLocaleString("ru-RU");
export const money = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : v.toLocaleString("ru-RU", { maximumFractionDigits: 2 }) + " ₽";
export const dt = (v: string | null | undefined, withTime = true) => {
  if (!v) return "—";
  const d = new Date(v);
  return d.toLocaleString("ru-RU", withTime
    ? { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }
    : { day: "2-digit", month: "2-digit", year: "2-digit" });
};
export const ago = (v: string | null | undefined) => {
  if (!v) return "—";
  const m = Math.round((Date.now() - new Date(v).getTime()) / 60000);
  if (m < 60) return `${m} мин`;
  if (m < 60 * 48) return `${Math.round(m / 60)} ч`;
  return `${Math.round(m / 1440)} дн`;
};

const DONOR: Record<string, [string, string]> = {
  new: ["blue", "новый"], monitored: ["green", "на мониторе"],
  paused: ["yellow", "пауза"], unclassified: ["grape", "неразобранный"],
};
const POST: Record<string, [string, string]> = {
  active: ["green", "в сборе"], frozen: ["yellow", "заморожен"],
  excluded: ["gray", "не берём"], forced: ["teal", "взят вручную"],
};
const JOB: Record<string, [string, string]> = {
  queued: ["yellow", "очередь"], running: ["blue", "в работе"], done: ["green", "готово"],
  error: ["red", "ошибка"], finished: ["gray", "завершено"],
};
const STAGE: Record<string, [string, string]> = {
  collecting: ["blue", "сбор"], filtering: ["blue", "фильтр f1"], classifying: ["blue", "ИИ: кто и где"],
  ready: ["green", "готово к распределению"], distributed: ["gray", "распределено"], error: ["red", "ошибка"],
};

export function StatusBadge({ kind, value }: { kind: "donor" | "post" | "job" | "stage"; value: string }) {
  const map = { donor: DONOR, post: POST, job: JOB, stage: STAGE }[kind];
  const [color, label] = map[value] || ["gray", value];
  return <Badge color={color} variant="light" size="sm">{label}</Badge>;
}

export function Kpi({ value, label, hint }: { value: React.ReactNode; label: string; hint?: string }) {
  return (
    <Paper p="sm">
      <div className="kpi">{value}</div>
      <Text size="xs" c="dimmed" mt={4}>{label}</Text>
      {hint && <Text size="xs" c="dimmed">{hint}</Text>}
    </Paper>
  );
}

export function KpiRow({ children }: { children: React.ReactNode }) {
  return <Group grow align="stretch" mb="md">{children}</Group>;
}

export type City = {
  id: number; name: string; is_active: boolean;
  donors_new: number; donors_monitored: number; donors_paused: number;
  posts: number; posts_active: number; posts_selling: number;
  leads: number; leads_unprobed: number; leads_with_phone: number; leads_sent: number;
  cost_per_contact: number; cost_per_handling: number;
  comment_fresh_days: number; post_freeze_days: number; donor_pause_days: number; resend_after_days: number;
  probe_mode: string; probe_enabled: boolean; probe_hook_token_set: boolean;
  crm_webhook_url: string; crm_secret_set: boolean; send_mode: string;
};

export function useCities() {
  return useQuery({
    queryKey: ["cities"],
    queryFn: () => api<{ cities: City[]; unclassified_donors: number }>("/cities"),
  });
}

/** Города для селектов: активные первыми, потом остальные с донорами. */
export function cityOptions(cities: City[] | undefined, withAll = true) {
  const opts = (cities || [])
    .filter((c) => c.is_active || c.donors_new + c.donors_monitored + c.donors_paused > 0)
    .map((c) => ({ value: String(c.id), label: c.is_active ? c.name : `${c.name} (выкл)` }));
  return withAll ? [{ value: "", label: "все города" }, ...opts] : opts;
}
