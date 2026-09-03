import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ActionIcon, Button, Checkbox, Group, Menu, Modal, Paper, Select, Table, Text, TextInput, Title, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconDotsVertical, IconPlus, IconSparkles } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, qs } from "../api";
import { StatusBadge, cityOptions, money, n, useCities } from "../ui";

const STATUS = [
  { value: "", label: "все статусы" }, { value: "new", label: "новые" }, { value: "monitored", label: "на мониторе" },
  { value: "paused", label: "пауза" }, { value: "unclassified", label: "неразобранные" },
];
const SORT = [
  { value: "leads", label: "лидов за период" }, { value: "comments", label: "комментов за период" },
  { value: "new_posts", label: "новых постов" }, { value: "posts", label: "постов всего" },
  { value: "added", label: "дата добавления" }, { value: "username", label: "логин" },
];

const STAGE_LABEL: Record<string, string> = { posts: "ждёт сбора постов", posts_run: "собираем посты", ai: "разметка ИИ", comments: "первый сбор комментариев", error: "ошибка" };

export default function Donors() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const cities = useCities();
  const [city, setCity] = useState("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState("leads");
  const [days, setDays] = useState("7");
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<number[]>([]);
  const [addOpen, setAddOpen] = useState(false);
  const [newLogin, setNewLogin] = useState("");
  const [newCity, setNewCity] = useState("");

  const params = { city_id: status === "unclassified" ? "" : city, unclassified: status === "unclassified" ? "true" : "",
    status: status === "unclassified" ? "" : status, sort, days, q, limit: 200 };
  const list = useQuery({ queryKey: ["donors", params], queryFn: () => api(`/donors${qs(params)}`) });

  const restart = useMutation({
    mutationFn: (id: number) => api(`/ops/donors/${id}/restart-intake`, { method: "POST" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["donors"] }); notifications.show({ color: "green", message: "Заведение перезапущено: посты → ИИ → сбор" }); },
    onError: (e: any) => notifications.show({ color: "red", message: e.message }),
  });
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: number; body: any }) => api(`/donors/${id}`, { method: "PATCH", body }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["donors"] }); qc.invalidateQueries({ queryKey: ["cities"] }); },
    onError: (e: any) => notifications.show({ color: "red", message: e.message }),
  });
  const add = useMutation({
    mutationFn: () => api("/donors", { method: "POST", body: { username: newLogin, city_id: newCity ? Number(newCity) : null } }),
    onSuccess: () => { setAddOpen(false); setNewLogin(""); qc.invalidateQueries({ queryKey: ["donors"] }); notifications.show({ color: "green", message: "Донор добавлен — разовая петля запустится сама" }); },
    onError: (e: any) => notifications.show({ color: "red", message: e.message }),
  });
  const recommend = useMutation({
    mutationFn: () => api("/search/tasks", { method: "POST", body: { kind: "recommendation", seed_donor_ids: sel } }),
    onSuccess: () => { setSel([]); notifications.show({ color: "green", message: "Задача поиска по рекомендациям создана" }); nav("/search"); },
    onError: (e: any) => notifications.show({ color: "red", message: e.message }),
  });

  const items: any[] = list.data?.items || [];
  const cityOpts = cityOptions(cities.data?.cities);

  return (
    <>
      <Group justify="space-between" mb="md">
        <div><Title order={2}>Доноры</Title><Text c="dimmed" size="sm">{list.data ? `${n(list.data.total)} шт. · период ${days} дн` : ""}</Text></div>
        <Group>
          <Button variant="light" leftSection={<IconSparkles size={16} />} disabled={!sel.length} loading={recommend.isPending} onClick={() => recommend.mutate()}>
            Выбранных → в рекомендации{sel.length ? ` (${sel.length})` : ""}
          </Button>
          <Button leftSection={<IconPlus size={16} />} onClick={() => setAddOpen(true)}>Донор вручную</Button>
        </Group>
      </Group>

      <Group mb="sm" gap="xs">
        <Select size="xs" w={200} value={city} onChange={(v) => setCity(v || "")} data={cityOpts} disabled={status === "unclassified"} />
        <Select size="xs" w={170} value={status} onChange={(v) => setStatus(v || "")} data={STATUS} />
        <Select size="xs" w={130} value={days} onChange={(v) => setDays(v || "7")} data={[{ value: "1", label: "1 день" }, { value: "7", label: "7 дней" }, { value: "30", label: "30 дней" }, { value: "90", label: "90 дней" }]} />
        <Select size="xs" w={190} value={sort} onChange={(v) => setSort(v || "leads")} data={SORT} />
        <TextInput size="xs" w={200} placeholder="логин…" value={q} onChange={(e) => setQ(e.currentTarget.value)} />
      </Group>

      <Paper style={{ overflowX: "auto" }}>
        <Table>
          <Table.Thead><Table.Tr>
            <Table.Th><Checkbox size="xs" checked={!!items.length && sel.length === items.length} indeterminate={!!sel.length && sel.length < items.length}
              onChange={(e) => setSel(e.currentTarget.checked ? items.map((d) => d.id) : [])} /></Table.Th>
            <Table.Th>Логин</Table.Th><Table.Th>Город</Table.Th><Table.Th>Статус</Table.Th><Table.Th>Подп.</Table.Th>
            <Table.Th>Постов</Table.Th><Table.Th>Продающих</Table.Th><Table.Th>Новых</Table.Th><Table.Th>Комментов</Table.Th>
            <Table.Th>Лидов</Table.Th><Table.Th>Пробито</Table.Th><Table.Th>Заявок</Table.Th><Table.Th>Стоимость лида</Table.Th><Table.Th />
          </Table.Tr></Table.Thead>
          <Table.Tbody>
            {items.map((d) => (
              <Table.Tr key={d.id}>
                <Table.Td><Checkbox size="xs" checked={sel.includes(d.id)} onChange={(e) => setSel(e.currentTarget.checked ? [...sel, d.id] : sel.filter((x) => x !== d.id))} /></Table.Td>
                <Table.Td><a className="rowlink mono" href={`https://instagram.com/${d.username}`} target="_blank" rel="noreferrer">{d.username}</a>{d.full_name && <Text size="xs" c="dimmed" className="clip">{d.full_name}</Text>}</Table.Td>
                <Table.Td>{d.city || <Text span c="dimmed">—</Text>}</Table.Td>
                <Table.Td><StatusBadge kind="donor" value={d.status} />{(d.status === "new" || d.status === "unclassified") && d.intake_stage && d.intake_stage !== "done" && <Text size="xs" c={d.intake_stage === "error" ? "red" : "dimmed"}>{STAGE_LABEL[d.intake_stage] || d.intake_stage}</Text>}{(d.intake_stage === "error" || d.status === "paused") && d.status_reason && <Text size="xs" c="dimmed" className="clip">{d.status_reason}</Text>}</Table.Td>
                <Table.Td className="num">{n(d.followers)}</Table.Td>
                <Table.Td className="num">{n(d.posts)}</Table.Td>
                <Table.Td className="num">{d.posts ? `${n(d.selling)} · ${Math.round(d.selling / d.posts * 100)}%` : "—"}</Table.Td>
                <Table.Td className="num">{n(d.new_posts_period)}</Table.Td>
                <Table.Td className="num">{n(d.comments_period)}</Table.Td>
                <Table.Td className="num">{n(d.leads_period)}</Table.Td>
                <Table.Td className="num">{n(d.probed_period)}</Table.Td>
                <Table.Td className="num">{n(d.applications)}</Table.Td>
                <Table.Td className="num">{money(d.cost_per_lead)}</Table.Td>
                <Table.Td>
                  <Menu shadow="md" width={220} position="bottom-end">
                    <Menu.Target><ActionIcon variant="subtle" color="gray"><IconDotsVertical size={16} /></ActionIcon></Menu.Target>
                    <Menu.Dropdown>
                      {d.status !== "paused" && <Menu.Item onClick={() => patch.mutate({ id: d.id, body: { status: "paused", status_reason: "пауза вручную" } })}>Пауза (снять с монитора)</Menu.Item>}
                      {d.status === "paused" && <Menu.Item onClick={() => patch.mutate({ id: d.id, body: { status: "monitored", status_reason: "возвращён вручную" } })}>Вернуть на монитор</Menu.Item>}
                      {(d.intake_stage === "error" || d.status === "paused") && <Menu.Item onClick={() => restart.mutate(d.id)}>Перезапустить заведение</Menu.Item>}
                      <Menu.Label>Перенести в город</Menu.Label>
                      {cityOpts.filter((c) => c.value && c.value !== String(d.city_id)).slice(0, 8).map((c) => (
                        <Menu.Item key={c.value} onClick={() => patch.mutate({ id: d.id, body: { city_id: Number(c.value) } })}>{c.label}</Menu.Item>
                      ))}
                    </Menu.Dropdown>
                  </Menu>
                </Table.Td>
              </Table.Tr>
            ))}
            {!items.length && <Table.Tr><Table.Td colSpan={14}><Text c="dimmed" ta="center">{list.isLoading ? "загрузка…" : "по фильтру никого"}</Text></Table.Td></Table.Tr>}
          </Table.Tbody>
        </Table>
      </Paper>

      <Modal opened={addOpen} onClose={() => setAddOpen(false)} title="Донор вручную">
        <TextInput label="Логин инстаграма" placeholder="profilady" value={newLogin} onChange={(e) => setNewLogin(e.currentTarget.value)} mb="sm" autoFocus />
        <Select label="Город" description="пусто — неразобранный: город постам поставит ИИ" value={newCity} onChange={(v) => setNewCity(v || "")} data={cityOpts.map((c) => c.value ? c : { value: "", label: "без города" })} mb="md" />
        <Button fullWidth loading={add.isPending} disabled={!newLogin.trim()} onClick={() => add.mutate()}>Добавить</Button>
      </Modal>
    </>
  );
}
