import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ActionIcon, Button, Checkbox, Group, Menu, Modal, Pagination, Paper, Select, Table, Text, TextInput, Title } from "@mantine/core";
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
const PAGE_SIZES = ["20", "50", "100"];

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
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(() => { try { return localStorage.getItem("donors_page_size") || "20"; } catch { return "20"; } });
  const [sel, setSel] = useState<number[]>([]);
  const [addOpen, setAddOpen] = useState(false);
  const [newLogin, setNewLogin] = useState("");
  const [newCity, setNewCity] = useState("");

  useEffect(() => { setPage(1); }, [city, status, sort, days, q, size]);
  const limit = Number(size) || 20;
  const params = { city_id: status === "unclassified" ? "" : city, unclassified: status === "unclassified" ? "true" : "",
    status: status === "unclassified" ? "" : status, sort, days, q, limit, offset: (page - 1) * limit };
  const list = useQuery({ queryKey: ["donors", params], queryFn: () => api(`/donors${qs(params)}`) });

  const err = (e: any) => notifications.show({ color: "red", message: e.message });
  const bust = () => { qc.invalidateQueries({ queryKey: ["donors"] }); qc.invalidateQueries({ queryKey: ["cities"] }); };
  const restart = useMutation({
    mutationFn: (id: number) => api(`/ops/donors/${id}/restart-intake`, { method: "POST" }),
    onSuccess: () => { bust(); notifications.show({ color: "green", message: "Заведение перезапущено: посты → ИИ → сбор" }); }, onError: err,
  });
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: number; body: any }) => api(`/donors/${id}`, { method: "PATCH", body }),
    onSuccess: bust, onError: err,
  });
  const add = useMutation({
    mutationFn: () => api("/donors", { method: "POST", body: { username: newLogin, city_id: newCity ? Number(newCity) : null } }),
    onSuccess: () => { setAddOpen(false); setNewLogin(""); bust(); notifications.show({ color: "green", message: "Донор добавлен — посты соберутся, если в городе включён сбор" }); }, onError: err,
  });
  const recommend = useMutation({
    mutationFn: () => api("/search/tasks", { method: "POST", body: { kind: "recommendation", seed_donor_ids: sel } }),
    onSuccess: () => { setSel([]); notifications.show({ color: "green", message: "Задача поиска по рекомендациям создана" }); nav("/search"); }, onError: err,
  });

  const items: any[] = list.data?.items || [];
  const total: number = list.data?.total || 0;
  const pages = Math.max(1, Math.ceil(total / limit));
  const cityOpts = cityOptions(cities.data?.cities);
  const changeSize = (v: string | null) => { const s = v || "20"; setSize(s); try { localStorage.setItem("donors_page_size", s); } catch {} };

  return (
    <>
      <Group justify="space-between" mb="sm">
        <div><Title order={2}>Доноры</Title><Text c="dimmed" size="sm">{list.data ? `${n(total)} шт. · период ${days} дн` : ""}</Text></div>
        <Group gap="xs">
          <Button size="xs" variant="light" leftSection={<IconSparkles size={14} />} disabled={!sel.length} loading={recommend.isPending} onClick={() => recommend.mutate()}>
            Выбранных → в рекомендации{sel.length ? ` (${sel.length})` : ""}
          </Button>
          <Button size="xs" leftSection={<IconPlus size={14} />} onClick={() => setAddOpen(true)}>Донор вручную</Button>
        </Group>
      </Group>

      <Group mb="xs" gap={6}>
        <Select size="xs" w={180} value={city} onChange={(v) => setCity(v || "")} data={cityOpts} disabled={status === "unclassified"} />
        <Select size="xs" w={150} value={status} onChange={(v) => setStatus(v || "")} data={STATUS} />
        <Select size="xs" w={110} value={days} onChange={(v) => setDays(v || "7")} data={[{ value: "1", label: "1 день" }, { value: "7", label: "7 дней" }, { value: "30", label: "30 дней" }, { value: "90", label: "90 дней" }]} />
        <Select size="xs" w={170} value={sort} onChange={(v) => setSort(v || "leads")} data={SORT} />
        <TextInput size="xs" w={160} placeholder="логин…" value={q} onChange={(e) => setQ(e.currentTarget.value)} />
      </Group>

      <Paper p="xs" style={{ overflowX: "auto" }}>
        <Table fz="xs" verticalSpacing={5} horizontalSpacing="xs" className="compact">
          <Table.Thead><Table.Tr>
            <Table.Th w={28}><Checkbox size="xs" checked={!!items.length && sel.length === items.length} indeterminate={!!sel.length && sel.length < items.length}
              onChange={(e) => setSel(e.currentTarget.checked ? items.map((d) => d.id) : [])} /></Table.Th>
            <Table.Th>Логин</Table.Th><Table.Th>Город</Table.Th><Table.Th>Статус</Table.Th><Table.Th ta="right">Подп.</Table.Th>
            <Table.Th ta="right">Постов<br /><span className="muted">продающих</span></Table.Th><Table.Th ta="right">Новых</Table.Th><Table.Th ta="right">Комм.</Table.Th>
            <Table.Th ta="right">Лидов</Table.Th><Table.Th ta="right">Пробито</Table.Th><Table.Th ta="right">Заявок</Table.Th><Table.Th ta="right">₽/лид</Table.Th><Table.Th w={28} />
          </Table.Tr></Table.Thead>
          <Table.Tbody>
            {items.map((d) => (
              <Table.Tr key={d.id}>
                <Table.Td><Checkbox size="xs" checked={sel.includes(d.id)} onChange={(e) => setSel(e.currentTarget.checked ? [...sel, d.id] : sel.filter((x) => x !== d.id))} /></Table.Td>
                <Table.Td>
                  <a className="rowlink mono" href={`https://instagram.com/${d.username}`} target="_blank" rel="noreferrer">{d.username}</a>
                  {d.full_name && <Text size="xs" c="dimmed" className="clip" style={{ maxWidth: 260 }} title={d.full_name}>{d.full_name}</Text>}
                </Table.Td>
                <Table.Td style={{ whiteSpace: "nowrap" }}>{d.city || <Text span c="dimmed">—</Text>}</Table.Td>
                <Table.Td style={{ whiteSpace: "nowrap" }}>
                  <StatusBadge kind="donor" value={d.status} />
                  {(d.status === "new" || d.status === "unclassified") && d.intake_stage && d.intake_stage !== "done" && <Text size="xs" c={d.intake_stage === "error" ? "red" : "dimmed"}>{STAGE_LABEL[d.intake_stage] || d.intake_stage}</Text>}
                  {(d.intake_stage === "error" || d.status === "paused") && d.status_reason && <Text size="xs" c="dimmed" className="clip" style={{ maxWidth: 180 }} title={d.status_reason}>{d.status_reason}</Text>}
                </Table.Td>
                <Table.Td className="num" ta="right">{n(d.followers)}</Table.Td>
                <Table.Td className="num" ta="right">{n(d.posts)}{d.posts ? <span className="muted"> · {n(d.selling)} ({Math.round(d.selling / d.posts * 100)}%)</span> : null}</Table.Td>
                <Table.Td className="num" ta="right">{n(d.new_posts_period)}</Table.Td>
                <Table.Td className="num" ta="right">{n(d.comments_period)}</Table.Td>
                <Table.Td className="num" ta="right">{n(d.leads_period)}</Table.Td>
                <Table.Td className="num" ta="right">{n(d.probed_period)}</Table.Td>
                <Table.Td className="num" ta="right">{n(d.applications)}</Table.Td>
                <Table.Td className="num" ta="right">{money(d.cost_per_lead)}</Table.Td>
                <Table.Td>
                  <Menu shadow="md" width={220} position="bottom-end">
                    <Menu.Target><ActionIcon size="sm" variant="subtle" color="gray"><IconDotsVertical size={14} /></ActionIcon></Menu.Target>
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
            {!items.length && <Table.Tr><Table.Td colSpan={13}><Text c="dimmed" ta="center">{list.isLoading ? "загрузка…" : "по фильтру никого"}</Text></Table.Td></Table.Tr>}
          </Table.Tbody>
        </Table>
        <Group justify="space-between" mt="xs">
          <Group gap="xs">
            <Text size="xs" c="dimmed">на странице</Text>
            <Select size="xs" w={80} value={size} onChange={changeSize} data={PAGE_SIZES} />
            <Text size="xs" c="dimmed">{total ? `${(page - 1) * limit + 1}–${Math.min(page * limit, total)} из ${n(total)}` : ""}</Text>
          </Group>
          <Pagination size="sm" value={page} onChange={setPage} total={pages} siblings={1} boundaries={1} />
        </Group>
      </Paper>

      <Modal opened={addOpen} onClose={() => setAddOpen(false)} title="Донор вручную">
        <TextInput label="Логин инстаграма" placeholder="profilady" value={newLogin} onChange={(e) => setNewLogin(e.currentTarget.value)} mb="sm" autoFocus />
        <Select label="Город" description="пусто — неразобранный: город постам поставит ИИ" value={newCity} onChange={(v) => setNewCity(v || "")} data={cityOpts.map((c) => c.value ? c : { value: "", label: "без города" })} mb="md" />
        <Button fullWidth loading={add.isPending} disabled={!newLogin.trim()} onClick={() => add.mutate()}>Добавить</Button>
      </Modal>
    </>
  );
}
