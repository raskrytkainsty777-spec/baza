import { useState } from "react";
import { Badge, Button, Group, Paper, SegmentedControl, Select, Table, Tabs, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, qs } from "../api";
import { Kpi, KpiRow, StatusBadge, ago, dt, n } from "../ui";

const KINDS = [
  { value: "", label: "все типы" }, { value: "search", label: "поиск" }, { value: "filter", label: "фильтр f1" },
  { value: "posts_intake", label: "посты донора" }, { value: "comments", label: "комментарии" },
  { value: "apify_new_posts", label: "Apify: новые посты" }, { value: "apify_counters", label: "Apify: счётчики" },
  { value: "apify_comments", label: "Apify: свежие комменты" }, { value: "apify_recommend", label: "Apify: рекомендации" },
];

export default function Jobs() {
  const qc = useQueryClient();
  const [state, setState] = useState("active");
  const [kind, setKind] = useState("");
  const [level, setLevel] = useState("");
  const jobs = useQuery({ queryKey: ["jobs", state, kind], queryFn: () => api(`/jobs${qs({ state, kind })}`), refetchInterval: 15_000 });
  const events = useQuery({ queryKey: ["events", level], queryFn: () => api(`/events${qs({ level, limit: 200 })}`), refetchInterval: 30_000 });
  const finish = useMutation({
    mutationFn: (id: number) => api(`/jobs/${id}/finish`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
    onError: (e: any) => notifications.show({ color: "red", message: e.message }),
  });
  const j = jobs.data;

  return (
    <>
      <Title order={2} mb="md">Задания и журнал</Title>
      {j && <KpiRow>
        <Kpi value={`${j.parserim.lines_busy}`} label="строк parser.im занято" hint={`в очереди заданий: ${j.parserim.queued}`} />
        <Kpi value={`$${j.apify.spent_today_usd.toFixed(2)}`} label="Apify потрачено сегодня" />
        <Kpi value={n((j.items || []).filter((x: any) => x.state === "running").length)} label="в работе сейчас" />
      </KpiRow>}

      <Tabs defaultValue="jobs">
        <Tabs.List mb="sm"><Tabs.Tab value="jobs">Задания</Tabs.Tab><Tabs.Tab value="events">Журнал</Tabs.Tab></Tabs.List>

        <Tabs.Panel value="jobs">
          <Group mb="sm" gap="xs">
            <SegmentedControl size="xs" value={state} onChange={setState} data={[{ value: "active", label: "активные" }, { value: "done", label: "готовые" }, { value: "error", label: "ошибки" }, { value: "all", label: "все" }]} />
            <Select size="xs" w={220} value={kind} onChange={(v) => setKind(v || "")} data={KINDS} />
          </Group>
          <Paper style={{ overflowX: "auto" }}>
            <Table>
              <Table.Thead><Table.Tr><Table.Th>Тип</Table.Th><Table.Th>Что</Table.Th><Table.Th>Кто</Table.Th><Table.Th>Строк</Table.Th><Table.Th>Статус</Table.Th><Table.Th>Собрано / в базу</Table.Th><Table.Th>$</Table.Th><Table.Th>Создано</Table.Th><Table.Th /></Table.Tr></Table.Thead>
              <Table.Tbody>
                {(j?.items || []).map((x: any) => (
                  <Table.Tr key={x.id}>
                    <Table.Td><Text size="sm">{KINDS.find((k) => k.value === x.kind)?.label || x.kind}</Text></Table.Td>
                    <Table.Td><Text size="sm" className="clip">{x.purpose}</Text>{x.error && <Text size="xs" c="red">{x.error}</Text>}</Table.Td>
                    <Table.Td><Badge size="xs" variant="light" color={x.provider === "apify" ? "cyan" : "grape"}>{x.provider}{x.external_id ? ` · ${x.external_id}` : ""}</Badge></Table.Td>
                    <Table.Td className="num">{x.provider === "parserim" ? x.lines : "—"}</Table.Td>
                    <Table.Td><StatusBadge kind="job" value={x.state} /></Table.Td>
                    <Table.Td className="num">{n(x.count)} / {n(x.rows_imported)}</Table.Td>
                    <Table.Td className="num">{x.cost_usd != null ? x.cost_usd.toFixed(3) : "—"}</Table.Td>
                    <Table.Td className="num">{dt(x.created_at)} <span className="muted">({ago(x.created_at)})</span></Table.Td>
                    <Table.Td>{(x.state === "queued" || x.state === "running") && <Button size="compact-xs" variant="subtle" color="gray" onClick={() => finish.mutate(x.id)}>Завершить</Button>}</Table.Td>
                  </Table.Tr>
                ))}
                {!j?.items?.length && <Table.Tr><Table.Td colSpan={9}><Text c="dimmed" ta="center">заданий нет</Text></Table.Td></Table.Tr>}
              </Table.Tbody>
            </Table>
            <Text size="xs" c="dimmed" mt="xs">Приоритет строк parser.im: досбор по приросту → посты новых доноров → фильтр f1 → поиск. Планировщик режет по числу строк из настроек.</Text>
          </Paper>
        </Tabs.Panel>

        <Tabs.Panel value="events">
          <Group mb="sm"><SegmentedControl size="xs" value={level} onChange={setLevel} data={[{ value: "", label: "всё" }, { value: "warn", label: "внимание" }, { value: "error", label: "ошибки" }]} /></Group>
          <Paper style={{ overflowX: "auto" }}>
            <Table>
              <Table.Thead><Table.Tr><Table.Th>Когда</Table.Th><Table.Th>Что</Table.Th><Table.Th>Сущность</Table.Th><Table.Th>Сообщение</Table.Th></Table.Tr></Table.Thead>
              <Table.Tbody>
                {(events.data?.items || []).map((e: any) => (
                  <Table.Tr key={e.id}>
                    <Table.Td className="num">{dt(e.at)}</Table.Td>
                    <Table.Td><Badge size="xs" variant="light" color={e.level === "error" ? "red" : e.level === "warn" ? "yellow" : "gray"}>{e.kind}</Badge></Table.Td>
                    <Table.Td className="mono">{e.entity ? `${e.entity} #${e.entity_id}` : "—"}</Table.Td>
                    <Table.Td><Text size="sm">{e.message}</Text></Table.Td>
                  </Table.Tr>
                ))}
                {!events.data?.items?.length && <Table.Tr><Table.Td colSpan={4}><Text c="dimmed" ta="center">событий пока нет</Text></Table.Td></Table.Tr>}
              </Table.Tbody>
            </Table>
          </Paper>
        </Tabs.Panel>
      </Tabs>
    </>
  );
}
