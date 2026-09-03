import { useState } from "react";
import { Link } from "react-router-dom";
import { Group, Paper, SegmentedControl, Select, Table, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { api, qs } from "../api";
import { cityOptions, money, n, useCities } from "../ui";

export default function Dashboard() {
  const [period, setPeriod] = useState("7d");
  const [hookBy, setHookBy] = useState<"hook" | "category">("hook");
  const cities = useCities();
  const [cityId, setCityId] = useState<string>("");
  const active = cities.data?.cities.find((c) => c.is_active);
  const cid = cityId || (active ? String(active.id) : "");

  const dash = useQuery({ queryKey: ["dashboard", period], queryFn: () => api(`/dashboard${qs({ period })}`) });
  const daily = useQuery({ queryKey: ["daily", cid], queryFn: () => api(`/dashboard/daily${qs({ city_id: cid, days: 14 })}`), enabled: !!cid });
  const hooks = useQuery({
    queryKey: ["hooks", cid, hookBy, period],
    queryFn: () => api(`/dashboard/hooks${qs({ city_id: cid, by: hookBy, days: period === "today" ? 1 : period === "30d" ? 30 : period === "90d" ? 90 : 7 })}`),
    enabled: !!cid,
  });

  const rows = (dash.data?.cities || []).filter((c: any) => c.is_active || c.donors_new + c.donors_monitored + c.donors_paused > 0);

  return (
    <>
      <Group justify="space-between" mb="md">
        <div>
          <Title order={2}>Мастер задач</Title>
          <Text c="dimmed" size="sm">по городам · {dash.data ? `неразобранных доноров: ${dash.data.unclassified_donors}` : ""}</Text>
        </div>
        <SegmentedControl value={period} onChange={setPeriod} data={[
          { value: "today", label: "сегодня" }, { value: "7d", label: "7 дней" },
          { value: "30d", label: "30 дней" }, { value: "90d", label: "90 дней" },
        ]} />
      </Group>

      <Paper mb="md" style={{ overflowX: "auto" }}>
        <Table>
          <Table.Thead><Table.Tr>
            <Table.Th>Город</Table.Th><Table.Th>Доноров<br /><span className="muted" style={{ textTransform: "none", fontWeight: 400 }}>новые / монитор / пауза</span></Table.Th>
            <Table.Th>Постов</Table.Th><Table.Th>Новых</Table.Th><Table.Th>С приростом</Table.Th><Table.Th>Комментов</Table.Th>
            <Table.Th>Лидов</Table.Th><Table.Th>Непробитых</Table.Th><Table.Th>Пробито</Table.Th><Table.Th>В CRM</Table.Th>
            <Table.Th>Заявки</Table.Th><Table.Th>Квал</Table.Th><Table.Th>Сделки</Table.Th>
            <Table.Th>CPL</Table.Th><Table.Th>CPQ</Table.Th><Table.Th>CPO</Table.Th>
          </Table.Tr></Table.Thead>
          <Table.Tbody>
            {rows.map((c: any) => (
              <Table.Tr key={c.city_id}>
                <Table.Td><Link className="rowlink" to={`/cities/${c.city_id}`}>{c.city}</Link>{!c.is_active && <Text span size="xs" c="dimmed"> выкл</Text>}</Table.Td>
                <Table.Td className="num">{c.donors_new} / {c.donors_monitored} / {c.donors_paused}</Table.Td>
                <Table.Td className="num">{n(c.posts_total)}</Table.Td>
                <Table.Td className="num">{n(c.new_posts)}</Table.Td>
                <Table.Td className="num">{n(c.growth_posts)}</Table.Td>
                <Table.Td className="num">{n(c.comments)}</Table.Td>
                <Table.Td className="num">{n(c.leads)}</Table.Td>
                <Table.Td className="num">{c.unprobed ? <Text span c="orange.8" fw={600}>{n(c.unprobed)}</Text> : 0}</Table.Td>
                <Table.Td className="num">{n(c.probed)}</Table.Td>
                <Table.Td className="num">{n(c.sent)}</Table.Td>
                <Table.Td className="num">{n(c.applications)}</Table.Td>
                <Table.Td className="num">{n(c.quals)}</Table.Td>
                <Table.Td className="num">{n(c.deals)}</Table.Td>
                <Table.Td className="num">{money(c.cpl)}</Table.Td>
                <Table.Td className="num">{money(c.cpq)}</Table.Td>
                <Table.Td className="num">{money(c.cpo)}</Table.Td>
              </Table.Tr>
            ))}
            {!rows.length && <Table.Tr><Table.Td colSpan={16}><Text c="dimmed" ta="center">{dash.isLoading ? "загрузка…" : "городов пока нет"}</Text></Table.Td></Table.Tr>}
          </Table.Tbody>
        </Table>
        <Text size="xs" c="dimmed" mt="xs">CPL / CPQ / CPO появятся, когда в городе заданы цены и пришли статусы из CRM. Затраты = пробитых × цена запроса + обработанных × цена обработки.</Text>
      </Paper>

      <Group align="flex-start" grow>
        <Paper>
          <Group justify="space-between" mb="xs">
            <Text fw={600}>Динамика по дням</Text>
            <Select size="xs" w={200} value={cid} onChange={(v) => setCityId(v || "")} data={cityOptions(cities.data?.cities, false)} />
          </Group>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>День</Table.Th><Table.Th>Новых постов</Table.Th><Table.Th>Комментов</Table.Th><Table.Th>Лидов</Table.Th><Table.Th>Пробито</Table.Th><Table.Th>В CRM</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>
              {(daily.data?.days || []).map((d: any) => (
                <Table.Tr key={d.day}><Table.Td className="num">{d.day}</Table.Td><Table.Td className="num">{n(d.new_posts)}</Table.Td><Table.Td className="num">{n(d.comments)}</Table.Td><Table.Td className="num">{n(d.leads)}</Table.Td><Table.Td className="num">{n(d.probed)}</Table.Td><Table.Td className="num">{n(d.sent)}</Table.Td></Table.Tr>
              ))}
              {!daily.data?.days?.length && <Table.Tr><Table.Td colSpan={6}><Text c="dimmed" ta="center">за 14 дней ничего не пришло — сбор ещё не запущен</Text></Table.Td></Table.Tr>}
            </Table.Tbody>
          </Table>
        </Paper>
        <Paper>
          <Group justify="space-between" mb="xs">
            <Text fw={600}>По крючкам</Text>
            <SegmentedControl size="xs" value={hookBy} onChange={(v) => setHookBy(v as any)} data={[{ value: "hook", label: "крючок" }, { value: "category", label: "категория" }]} />
          </Group>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>{hookBy === "hook" ? "Крючок" : "Категория"}</Table.Th><Table.Th>Постов</Table.Th><Table.Th>Комментов</Table.Th><Table.Th>Лидов</Table.Th><Table.Th>Заявок</Table.Th><Table.Th>CPL</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>
              {(hooks.data?.rows || []).slice(0, 14).map((r: any) => (
                <Table.Tr key={r.value}><Table.Td>{r.value}</Table.Td><Table.Td className="num">{n(r.posts)}</Table.Td><Table.Td className="num">{n(r.comments)}</Table.Td><Table.Td className="num">{n(r.leads)}</Table.Td><Table.Td className="num">{n(r.applications)}</Table.Td><Table.Td className="num">{money(r.cpl)}</Table.Td></Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Paper>
      </Group>
    </>
  );
}
