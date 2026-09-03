import { useState } from "react";
import { Badge, Button, Group, Paper, Select, Stack, Table, Text, Textarea, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconArrowRight } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, qs } from "../api";
import { StatusBadge, cityOptions, dt, n, useCities } from "../ui";

const STAGES = ["collecting", "filtering", "classifying", "ready"];

function Stages({ t }: { t: any }) {
  const idx = t.stage === "distributed" ? 4 : STAGES.indexOf(t.stage);
  const cell = (i: number, label: string, sub: string) => {
    const done = idx > i || t.stage === "distributed";
    const run = idx === i && t.stage !== "distributed";
    return (
      <Paper key={label} p="xs" withBorder style={{ minWidth: 150, borderColor: done ? "var(--mantine-color-green-5)" : run ? "var(--mantine-color-violet-5)" : undefined, background: done ? "var(--mantine-color-green-0)" : run ? "var(--mantine-color-violet-0)" : undefined }}>
        <Text size="sm" fw={600} c={done ? "green.9" : run ? "violet.9" : "dimmed"}>{label}</Text>
        <Text size="xs" c="dimmed" className="mono">{sub}</Text>
      </Paper>
    );
  };
  return (
    <Group gap={6} wrap="wrap">
      {cell(0, "собрано", `${n(t.collected)} авторов`)}<IconArrowRight size={14} color="gray" />
      {cell(1, "фильтр f1", `${n(t.passed)} прошли · ${n(t.rejected_inactive)} неактивны`)}<IconArrowRight size={14} color="gray" />
      {cell(2, "ИИ: кто и где", `${n(t.confident)} уверенно · ${n(t.unclear)} неясно · ${n(t.rejected_activity)} не те`)}<IconArrowRight size={14} color="gray" />
      {cell(3, "готово", t.stage === "distributed" ? `распределено ${n(t.distributed)}` : "к распределению")}
    </Group>
  );
}

export default function Search() {
  const qc = useQueryClient();
  const cities = useCities();
  const [kind, setKind] = useState("hashtag");
  const [text, setText] = useState("");
  const [assignCity, setAssignCity] = useState("");
  const [sel, setSel] = useState<number[]>([]);

  const tasks = useQuery({ queryKey: ["search-tasks"], queryFn: () => api("/search/tasks"), refetchInterval: 20_000 });
  const unclear = useQuery({ queryKey: ["candidates", "unclear"], queryFn: () => api(`/search/candidates${qs({ state: "unclear", limit: 200 })}`) });
  const bust = () => { qc.invalidateQueries({ queryKey: ["search-tasks"] }); qc.invalidateQueries({ queryKey: ["candidates"] }); qc.invalidateQueries({ queryKey: ["donors"] }); };
  const err = (e: any) => notifications.show({ color: "red", message: e.message });

  const create = useMutation({
    mutationFn: () => api("/search/tasks", { method: "POST", body: { kind, values: text.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean) } }),
    onSuccess: () => { setText(""); bust(); notifications.show({ color: "green", message: "Задача создана — сбор начнётся, когда освободятся строки parser.im" }); }, onError: err,
  });
  const distribute = useMutation({ mutationFn: (id: number) => api(`/search/tasks/${id}/distribute`, { method: "POST" }), onSuccess: (r: any) => { bust(); notifications.show({ color: "green", message: `В города ушло ${r.distributed}` }); }, onError: err });
  const assign = useMutation({ mutationFn: () => api("/search/candidates/assign", { method: "POST", body: { candidate_ids: sel, city_id: assignCity ? Number(assignCity) : null } }), onSuccess: () => { setSel([]); bust(); }, onError: err });
  const reject = useMutation({ mutationFn: () => api("/search/candidates/reject", { method: "POST", body: { candidate_ids: sel } }), onSuccess: () => { setSel([]); bust(); }, onError: err });

  const items: any[] = tasks.data?.items || [];
  const unc: any[] = unclear.data?.items || [];

  return (
    <>
      <Group justify="space-between" mb="md">
        <div><Title order={2}>Поиск доноров</Title><Text c="dimmed" size="sm">задачи идут сами: сбор → фильтр f1 → ИИ «кто и где» → распределение. Город — результат, не вход.</Text></div>
      </Group>

      <Paper mb="md">
        <Text fw={600} mb="xs">Новая задача</Text>
        <Group align="flex-start" gap="xs">
          <Select w={230} value={kind} onChange={(v) => setKind(v || "hashtag")} data={[{ value: "hashtag", label: "по тегам · parser.im" }, { value: "keyword", label: "по ключам · parser.im" }]} />
          <Textarea style={{ flex: 1 }} autosize minRows={1} placeholder={kind === "hashtag" ? "#риелтормосква, #новостройкимосквы, …" : "риелтор, агент по недвижимости, новостройки"} value={text} onChange={(e) => setText(e.currentTarget.value)} />
          <Button loading={create.isPending} disabled={!text.trim()} onClick={() => create.mutate()}>Запустить</Button>
        </Group>
        <Text size="xs" c="dimmed" mt="xs">Рекомендации Apify — из «Доноров»: отметьте сидов галочками и нажмите «Выбранных → в рекомендации». Уже известные аккаунты и отклонённые в задачу не попадают.</Text>
      </Paper>

      <Stack mb="md">
        {items.map((t) => (
          <Paper key={t.id}>
            <Group justify="space-between" mb="xs">
              <Group gap="xs"><Text fw={600}>{t.title}</Text><Badge size="xs" variant="light" color={t.kind === "recommendation" ? "cyan" : "grape"}>{t.kind === "recommendation" ? "Apify" : "parser.im"}</Badge><StatusBadge kind="stage" value={t.stage} /><Text size="xs" c="dimmed">{dt(t.created_at)}</Text></Group>
              {t.stage === "ready" && <Button size="xs" loading={distribute.isPending} onClick={() => distribute.mutate(t.id)}>Распределить по городам · {n(t.confident)}</Button>}
            </Group>
            <Stages t={t} />
            {t.error && <Text size="xs" c="red" mt="xs">{t.error}</Text>}
          </Paper>
        ))}
        {!items.length && <Paper><Text c="dimmed" ta="center">задач пока нет</Text></Paper>}
      </Stack>

      <Paper>
        <Group justify="space-between" mb="xs">
          <div><Text fw={600}>Неразобранные · {n(tasks.data?.unclear_total)}</Text><Text size="xs" c="dimmed">город неясен — «вся Россия», нет адреса, два города. Деятельность подходящая.</Text></div>
          <Group gap="xs">
            <Select size="xs" w={200} placeholder="в город…" value={assignCity} onChange={(v) => setAssignCity(v || "")} data={[{ value: "", label: "без города — город по постам" }, ...cityOptions(cities.data?.cities, false)]} />
            <Button size="xs" disabled={!sel.length} loading={assign.isPending} onClick={() => assign.mutate()}>{assignCity ? "В город" : "Собрать посты → город по постам"} · {sel.length}</Button>
            <Button size="xs" variant="subtle" color="red" disabled={!sel.length} loading={reject.isPending} onClick={() => reject.mutate()}>Отклонить</Button>
          </Group>
        </Group>
        <Table>
          <Table.Thead><Table.Tr><Table.Th /><Table.Th>Логин</Table.Th><Table.Th>Найден</Table.Th><Table.Th>Подп.</Table.Th><Table.Th>Описание</Table.Th><Table.Th>Адрес</Table.Th><Table.Th>ИИ: город</Table.Th><Table.Th>Почему неясно</Table.Th></Table.Tr></Table.Thead>
          <Table.Tbody>
            {unc.map((c) => (
              <Table.Tr key={c.id}>
                <Table.Td><input type="checkbox" checked={sel.includes(c.id)} onChange={(e) => setSel(e.currentTarget.checked ? [...sel, c.id] : sel.filter((x) => x !== c.id))} /></Table.Td>
                <Table.Td className="mono">{c.username}</Table.Td><Table.Td><Text size="xs">{c.found_by}</Text></Table.Td><Table.Td className="num">{n(c.followers)}</Table.Td>
                <Table.Td><Text size="xs" className="clip2">{c.bio}</Text></Table.Td><Table.Td><Text size="xs">{c.address || "—"}</Text></Table.Td>
                <Table.Td><Text size="xs">{c.city_name_raw || "—"} {c.city_confidence != null && <span className="muted">({Math.round(c.city_confidence * 100)}%)</span>}</Text></Table.Td>
                <Table.Td><Text size="xs" c="dimmed">{c.ai_reason}</Text></Table.Td>
              </Table.Tr>
            ))}
            {!unc.length && <Table.Tr><Table.Td colSpan={8}><Text c="dimmed" ta="center">пусто</Text></Table.Td></Table.Tr>}
          </Table.Tbody>
        </Table>
      </Paper>
    </>
  );
}
