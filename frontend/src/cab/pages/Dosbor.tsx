import { useState } from "react";
import { Badge, Button, Checkbox, Code, Group, Modal, MultiSelect, NumberInput, Paper, PasswordInput, Select, Stack, Switch, Table, Tabs, Text, TextInput, Textarea, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconPlus } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { SUPPLIER_LABEL, cabApi, cabToken } from "../api";
import { dt, money, n } from "../../ui";

const err = (e: any) => notifications.show({ color: "red", message: e.message });

function Agents() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["dos-agents"], queryFn: () => cabApi("/dosbor/agents"), refetchInterval: 30_000 });
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ login: "", password: "", name: "" });
  const [pay, setPay] = useState<any>(null);
  const [hist, setHist] = useState<any>(null);
  const bust = () => qc.invalidateQueries({ queryKey: ["dos-agents"] });
  const create = useMutation({ mutationFn: () => cabApi("/dosbor/agents", { method: "POST", body: f }), onSuccess: () => { setOpen(false); setF({ login: "", password: "", name: "" }); bust(); notifications.show({ color: "green", message: "Агент создан. Вход для него: " + location.origin + "/agent" }); }, onError: err });
  const patch = useMutation({ mutationFn: ({ id, body }: any) => cabApi(`/dosbor/agents/${id}`, { method: "PATCH", body }), onSuccess: bust, onError: err });
  const payout = useMutation({ mutationFn: (id: number) => cabApi(`/dosbor/agents/${id}/payout`, { method: "POST", body: {} }), onSuccess: (r: any) => { setPay(null); bust(); notifications.show({ color: "green", message: `Выплата ${money(r.paid)} записана, баланс обнулён` }); }, onError: err });
  const payouts = useQuery({ queryKey: ["dos-payouts", hist?.id], queryFn: () => cabApi(`/dosbor/agents/${hist.id}/payouts`), enabled: !!hist });
  const items: any[] = q.data?.items || [];
  const req = (r: any) => r ? `${r.kind === "sbp" ? "СБП" : "карта"} · ${r.bank} · ${r.value}` : "нет реквизитов";
  return (
    <>
      <Group justify="space-between" mb="xs"><Text c="dimmed" size="sm">агенты ищут новые номера-источники на сайтах из списка и получают оплату за каждый уникальный · вход агентов: <Code>{location.origin}/agent</Code></Text><Button size="xs" leftSection={<IconPlus size={14} />} onClick={() => setOpen(true)}>Добавить агента</Button></Group>
      <Paper p="xs">
        <Table fz="xs" verticalSpacing={5} className="compact">
          <Table.Thead><Table.Tr><Table.Th>Агент</Table.Th><Table.Th>Логин</Table.Th><Table.Th ta="right">Найдено</Table.Th><Table.Th ta="right">Баланс</Table.Th><Table.Th ta="right">Выплачено</Table.Th><Table.Th>Реквизиты</Table.Th><Table.Th /></Table.Tr></Table.Thead>
          <Table.Tbody>
            {items.map((a) => (
              <Table.Tr key={a.id} style={{ opacity: a.is_active ? 1 : 0.5 }}>
                <Table.Td>{a.name}</Table.Td><Table.Td className="mono">{a.login}</Table.Td>
                <Table.Td className="num" ta="right">{n(a.found_total)}</Table.Td><Table.Td className="num" ta="right"><Text span fw={600}>{money(a.balance)}</Text></Table.Td><Table.Td className="num" ta="right">{money(a.paid_total)}</Table.Td>
                <Table.Td><Text size="xs" c={a.requisites ? undefined : "red"}>{req(a.requisites)}</Text></Table.Td>
                <Table.Td><Group gap={4}>
                  <Button size="compact-xs" variant="light" disabled={!a.balance} onClick={() => setPay(a)}>Обнулить баланс</Button>
                  <Button size="compact-xs" variant="subtle" onClick={() => setHist(a)}>История</Button>
                  <Button size="compact-xs" variant="subtle" color="gray" onClick={() => { const p = prompt("Новый пароль агента (от 6 символов)"); if (p) patch.mutate({ id: a.id, body: { password: p } }); }}>Пароль</Button>
                  <Button size="compact-xs" variant="subtle" color={a.is_active ? "red" : "green"} onClick={() => patch.mutate({ id: a.id, body: { is_active: !a.is_active } })}>{a.is_active ? "Отключить" : "Включить"}</Button>
                </Group></Table.Td>
              </Table.Tr>
            ))}
            {!items.length && <Table.Tr><Table.Td colSpan={7}><Text c="dimmed" ta="center">агентов пока нет</Text></Table.Td></Table.Tr>}
          </Table.Tbody>
        </Table>
      </Paper>
      <Modal opened={open} onClose={() => setOpen(false)} title="Новый агент">
        <TextInput label="Имя" value={f.name} onChange={(e) => setF({ ...f, name: e.currentTarget.value })} />
        <TextInput label="Логин" value={f.login} onChange={(e) => setF({ ...f, login: e.currentTarget.value })} mt="xs" />
        <PasswordInput label="Пароль" description="от 6 символов, выдаёте агенту вместе со ссылкой" value={f.password} onChange={(e) => setF({ ...f, password: e.currentTarget.value })} mt="xs" />
        <Button fullWidth mt="md" loading={create.isPending} disabled={!f.name || !f.login || f.password.length < 6} onClick={() => create.mutate()}>Создать</Button>
      </Modal>
      <Modal opened={!!pay} onClose={() => setPay(null)} title={`Выплата: ${pay?.name}`}>
        {pay && <Stack gap="xs">
          <Text>Заработано на задачах: <b>{money(pay.balance)}</b></Text>
          <Text size="sm">Реквизиты: {req(pay.requisites)}</Text>
          <Text size="xs" c="dimmed">Переведите деньги руками, затем нажмите «Обнулить» — запишем выплату в историю и обнулим баланс агента.</Text>
          <Button loading={payout.isPending} onClick={() => payout.mutate(pay.id)}>Обнулить</Button>
        </Stack>}
      </Modal>
      <Modal opened={!!hist} onClose={() => setHist(null)} title={`История выплат: ${hist?.name}`}>
        <Table fz="xs"><Table.Thead><Table.Tr><Table.Th>Дата</Table.Th><Table.Th ta="right">Сумма</Table.Th><Table.Th>Реквизиты</Table.Th></Table.Tr></Table.Thead>
          <Table.Tbody>{(payouts.data?.items || []).map((p: any) => <Table.Tr key={p.id}><Table.Td className="num">{dt(p.paid_at)}</Table.Td><Table.Td className="num" ta="right">{money(p.amount)}</Table.Td><Table.Td><Text size="xs">{req(p.requisites)}</Text></Table.Td></Table.Tr>)}
            {!payouts.data?.items?.length && <Table.Tr><Table.Td colSpan={3}><Text c="dimmed" ta="center">выплат не было</Text></Table.Td></Table.Tr>}</Table.Tbody></Table>
      </Modal>
    </>
  );
}

function Lists() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["dos-lists"], queryFn: () => cabApi("/dosbor/lists") });
  const [open, setOpen] = useState(false);
  const [add, setAdd] = useState<any>(null);
  const [f, setF] = useState({ name: "", text: "", delimiter: ";" });
  const [more, setMore] = useState("");
  const [view, setView] = useState<any>(null);
  const res = useQuery({ queryKey: ["dos-res", view?.id], queryFn: () => cabApi(`/dosbor/lists/${view.id}/resources`), enabled: !!view });
  const bust = () => qc.invalidateQueries({ queryKey: ["dos-lists"] });
  const create = useMutation({ mutationFn: () => cabApi("/dosbor/lists", { method: "POST", body: f }), onSuccess: (r: any) => { setOpen(false); setF({ name: "", text: "", delimiter: ";" }); bust(); notifications.show({ color: "green", message: `Список создан, сайтов: ${r.resources}` }); }, onError: err });
  const append = useMutation({ mutationFn: () => cabApi(`/dosbor/lists/${add.id}/resources`, { method: "POST", body: { text: more, delimiter: ";" } }), onSuccess: (r: any) => { setAdd(null); setMore(""); bust(); notifications.show({ color: "green", message: `Добавлено сайтов: ${r.added}` }); }, onError: err });
  const items: any[] = q.data?.items || [];
  return (
    <>
      <Group justify="space-between" mb="xs"><Text c="dimmed" size="sm">списки сайтов для агентов: строка «сайт;компания» — компания нужна, чтобы агент привязал найденный номер к ней</Text><Button size="xs" leftSection={<IconPlus size={14} />} onClick={() => setOpen(true)}>Добавить ресурсы</Button></Group>
      <Paper p="xs">
        <Table fz="xs" verticalSpacing={5} className="compact">
          <Table.Thead><Table.Tr><Table.Th>Список</Table.Th><Table.Th ta="right">Сайтов</Table.Th><Table.Th ta="right">Компаний</Table.Th><Table.Th>Создан</Table.Th><Table.Th /></Table.Tr></Table.Thead>
          <Table.Tbody>
            {items.map((l) => <Table.Tr key={l.id}><Table.Td>{l.name}</Table.Td><Table.Td className="num" ta="right">{n(l.resources)}</Table.Td><Table.Td className="num" ta="right">{n(l.companies)}</Table.Td><Table.Td className="num">{dt(l.created_at)}</Table.Td><Table.Td><Group gap={4}><Button size="compact-xs" variant="subtle" onClick={() => setView(l)}>Показать</Button><Button size="compact-xs" variant="light" onClick={() => setAdd(l)}>Добавить сайты</Button></Group></Table.Td></Table.Tr>)}
            {!items.length && <Table.Tr><Table.Td colSpan={5}><Text c="dimmed" ta="center">списков пока нет</Text></Table.Td></Table.Tr>}
          </Table.Tbody>
        </Table>
      </Paper>
      <Modal opened={open} onClose={() => setOpen(false)} title="Новый список ресурсов" size="lg">
        <TextInput label="Название списка" value={f.name} onChange={(e) => setF({ ...f, name: e.currentTarget.value })} />
        <Textarea label="Сайты" description="по одному в строке: сайт;компания" autosize minRows={6} maxRows={16} value={f.text} onChange={(e) => setF({ ...f, text: e.currentTarget.value })} mt="xs" placeholder={"alye-parusa.ru;Алые паруса\nzhar-ptica.spb.ru;Жар-птица"} />
        <Button fullWidth mt="md" loading={create.isPending} disabled={!f.name.trim()} onClick={() => create.mutate()}>Создать</Button>
      </Modal>
      <Modal opened={!!add} onClose={() => setAdd(null)} title={`Добавить сайты: ${add?.name}`} size="lg">
        <Textarea autosize minRows={5} maxRows={16} value={more} onChange={(e) => setMore(e.currentTarget.value)} placeholder="сайт;компания" />
        <Button fullWidth mt="md" loading={append.isPending} disabled={!more.trim()} onClick={() => append.mutate()}>Добавить</Button>
      </Modal>
      <Modal opened={!!view} onClose={() => setView(null)} title={view?.name} size="lg">
        <Table fz="xs"><Table.Tbody>{(res.data?.items || []).map((r: any, i: number) => <Table.Tr key={r.id}><Table.Td className="num">{i + 1}</Table.Td><Table.Td><a href={r.url} target="_blank" rel="noreferrer" className="rowlink">{r.url}</a></Table.Td><Table.Td>{r.company || <Text span c="dimmed">без компании</Text>}</Table.Td></Table.Tr>)}</Table.Tbody></Table>
      </Modal>
    </>
  );
}

function TasksTab() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["dos-tasks"], queryFn: () => cabApi("/dosbor/tasks"), refetchInterval: 30_000 });
  const lists = useQuery({ queryKey: ["dos-lists"], queryFn: () => cabApi("/dosbor/lists") });
  const agents = useQuery({ queryKey: ["dos-agents"], queryFn: () => cabApi("/dosbor/agents") });
  const sup = useQuery({ queryKey: ["cab-suppliers"], queryFn: () => cabApi("/suppliers") });
  const [open, setOpen] = useState(false);
  const [edit, setEdit] = useState<any>(null);
  const [stat, setStat] = useState<any>(null);
  const blank = { name: "", list_id: "", agent_ids: [] as string[], price_per_source: 50, limit_sources: 100, to_purchase: false, purchase_limit: 5, purchase_suppliers: [] as string[] };
  const [f, setF] = useState<any>(blank);
  const bust = () => { qc.invalidateQueries({ queryKey: ["dos-tasks"] }); qc.invalidateQueries({ queryKey: ["dos-agents"] }); };
  const body = (x: any) => ({ name: x.name, list_id: Number(x.list_id), agent_ids: x.agent_ids.map(Number), price_per_source: Number(x.price_per_source) || 0, limit_sources: Number(x.limit_sources) || 0, to_purchase: !!x.to_purchase, purchase_limit: Number(x.purchase_limit) || 0, purchase_suppliers: x.purchase_suppliers });
  const create = useMutation({ mutationFn: () => cabApi("/dosbor/tasks", { method: "POST", body: body(f) }), onSuccess: () => { setOpen(false); setF(blank); bust(); }, onError: err });
  const patch = useMutation({ mutationFn: ({ id, b }: any) => cabApi(`/dosbor/tasks/${id}`, { method: "PATCH", body: b }), onSuccess: () => { setEdit(null); bust(); }, onError: err });
  const toBuy = useMutation({ mutationFn: (id: number) => cabApi(`/dosbor/tasks/${id}/to-purchase`, { method: "POST" }), onSuccess: (r: any) => { bust(); notifications.show({ color: "green", message: `В закупку ушло ${r.purchased}. ${r.note}` }); }, onError: err });
  const stats = useQuery({ queryKey: ["dos-stats", stat?.id], queryFn: () => cabApi(`/dosbor/tasks/${stat.id}/stats`), enabled: !!stat });
  const items: any[] = q.data?.items || [];
  const listOpts = (lists.data?.items || []).map((l: any) => ({ value: String(l.id), label: `${l.name} (${l.resources})` }));
  const agentOpts = (agents.data?.items || []).filter((a: any) => a.is_active).map((a: any) => ({ value: String(a.id), label: a.name }));
  const supOpts = (sup.data?.items || []).filter((s: any) => s.available).map((s: any) => ({ value: s.code, label: s.label }));
  const download = async (id: number, fmt: string) => {
    const res = await fetch(`/api/cab/dosbor/tasks/${id}/export?fmt=${fmt}`, { headers: { Authorization: `Bearer ${cabToken()}` } });
    const blob = await res.blob(); const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = fmt === "numbers" ? `task_${id}.txt` : `task_${id}.csv`; a.click();
  };
  const form = (x: any, set: (v: any) => void) => (
    <>
      <TextInput label="Название задачи" value={x.name} onChange={(e) => set({ ...x, name: e.currentTarget.value })} />
      {!x.id && <Select label="Список ресурсов" data={listOpts} value={x.list_id} onChange={(v) => set({ ...x, list_id: v || "" })} mt="xs" />}
      <MultiSelect label="Агенты" data={agentOpts} value={x.agent_ids} onChange={(v) => set({ ...x, agent_ids: v })} mt="xs" />
      <Group grow mt="xs">
        <NumberInput label="Цена за уникальный источник, ₽" value={x.price_per_source} onChange={(v) => set({ ...x, price_per_source: v })} min={0} />
        <NumberInput label="Лимит уникальных источников на задачу" description="0 — без лимита" value={x.limit_sources} onChange={(v) => set({ ...x, limit_sources: v })} min={0} />
      </Group>
      <Switch mt="sm" label="Новые уникальные источники сразу в закупку" checked={!!x.to_purchase} onChange={(e) => set({ ...x, to_purchase: e.currentTarget.checked })} />
      {x.to_purchase && <Group grow mt="xs">
        <NumberInput label="Лимит на связку для них" value={x.purchase_limit} onChange={(v) => set({ ...x, purchase_limit: v })} min={0} />
        <MultiSelect label="Поставщики" description="пусто — как в настройках" data={supOpts} value={x.purchase_suppliers} onChange={(v) => set({ ...x, purchase_suppliers: v })} />
      </Group>}
    </>
  );
  return (
    <>
      <Group justify="space-between" mb="xs"><Text c="dimmed" size="sm">лимит считается на задачу, не на агента · выключенная задача у агентов не видна</Text><Button size="xs" leftSection={<IconPlus size={14} />} onClick={() => setOpen(true)}>Создать задачу</Button></Group>
      <Stack>
        {items.map((t) => (
          <Paper key={t.id} p="sm">
            <Group justify="space-between">
              <div>
                <Group gap="xs"><Text fw={600}>{t.name}</Text><Badge size="xs" variant="light" color={t.enabled ? "green" : "gray"}>{t.enabled ? "активна" : "выключена"}</Badge>{t.to_purchase && <Badge size="xs" variant="light" color="teal">сразу в закупку · лимит {t.purchase_limit} · {(t.purchase_suppliers || []).join(", ") || "по умолчанию"}</Badge>}</Group>
                <Text size="xs" c="dimmed">список: {t.list_name} · агентов: {t.agent_ids.length} · {money(t.price_per_source)} за источник · создана {dt(t.created_at)}</Text>
              </div>
              <Group gap="md">
                <div style={{ textAlign: "right" }}><Text size="xl" fw={700} className="num">{n(t.found)}{t.limit_sources ? ` / ${n(t.limit_sources)}` : ""}</Text><Text size="xs" c="dimmed">найдено{t.limit_sources ? " / лимит" : ""}</Text></div>
                <Group gap={4}>
                  <Button size="compact-xs" variant="light" onClick={() => setStat(t)}>Статистика</Button>
                  <Button size="compact-xs" variant="light" onClick={() => setEdit({ ...t, agent_ids: t.agent_ids.map(String) })}>Изменить</Button>
                  <Button size="compact-xs" variant="subtle" onClick={() => download(t.id, "csv")}>CSV</Button>
                  <Button size="compact-xs" variant="subtle" onClick={() => download(t.id, "numbers")}>Номера</Button>
                  <Button size="compact-xs" variant="subtle" color="teal" loading={toBuy.isPending} onClick={() => toBuy.mutate(t.id)}>В закупку</Button>
                  <Button size="compact-xs" variant="subtle" color={t.enabled ? "red" : "green"} onClick={() => patch.mutate({ id: t.id, b: { enabled: !t.enabled } })}>{t.enabled ? "Выключить" : "Включить"}</Button>
                </Group>
              </Group>
            </Group>
          </Paper>
        ))}
        {!items.length && <Paper><Text c="dimmed" ta="center">задач пока нет — сначала список ресурсов и агенты</Text></Paper>}
      </Stack>
      <Modal opened={open} onClose={() => setOpen(false)} title="Новая задача" size="lg">
        {form(f, setF)}
        <Button fullWidth mt="md" loading={create.isPending} disabled={!f.name || !f.list_id} onClick={() => create.mutate()}>Создать и запустить</Button>
      </Modal>
      <Modal opened={!!edit} onClose={() => setEdit(null)} title="Изменить задачу" size="lg">
        {edit && <>{form(edit, setEdit)}<Button fullWidth mt="md" loading={patch.isPending} onClick={() => { const b: any = body(edit); delete b.list_id; patch.mutate({ id: edit.id, b }); }}>Сохранить</Button></>}
      </Modal>
      <Modal opened={!!stat} onClose={() => setStat(null)} title={`Статистика: ${stat?.name}`} size="lg">
        {stats.data && <>
          <Text size="sm" mb="xs">Найдено {n(stats.data.task.found)}{stats.data.task.limit_sources ? ` из ${n(stats.data.task.limit_sources)}` : ""} · в закупке {n(stats.data.purchased)}</Text>
          <Group align="flex-start" grow>
            <Table fz="xs"><Table.Thead><Table.Tr><Table.Th>Агент</Table.Th><Table.Th ta="right">Найдено</Table.Th></Table.Tr></Table.Thead><Table.Tbody>{stats.data.by_agent.map((r: any) => <Table.Tr key={r.agent}><Table.Td>{r.agent}</Table.Td><Table.Td className="num" ta="right">{r.count}</Table.Td></Table.Tr>)}</Table.Tbody></Table>
            <Table fz="xs"><Table.Thead><Table.Tr><Table.Th>День</Table.Th><Table.Th>Агент</Table.Th><Table.Th ta="right">Найдено</Table.Th></Table.Tr></Table.Thead><Table.Tbody>{stats.data.by_day.map((r: any, i: number) => <Table.Tr key={i}><Table.Td className="num">{r.day}</Table.Td><Table.Td>{r.agent}</Table.Td><Table.Td className="num" ta="right">{r.count}</Table.Td></Table.Tr>)}</Table.Tbody></Table>
          </Group>
        </>}
      </Modal>
    </>
  );
}

export default function Dosbor() {
  return (
    <>
      <Title order={2} mb={4}>Досбор</Title>
      <Text c="dimmed" size="sm" mb="md">агенты по вашим спискам сайтов находят новые номера-источники · поставщики: {Object.keys(SUPPLIER_LABEL).slice(0, 3).join(", ")}</Text>
      <Tabs defaultValue="tasks">
        <Tabs.List mb="sm"><Tabs.Tab value="tasks">Задачи</Tabs.Tab><Tabs.Tab value="lists">Ресурсы</Tabs.Tab><Tabs.Tab value="agents">Агенты</Tabs.Tab></Tabs.List>
        <Tabs.Panel value="tasks"><TasksTab /></Tabs.Panel>
        <Tabs.Panel value="lists"><Lists /></Tabs.Panel>
        <Tabs.Panel value="agents"><Agents /></Tabs.Panel>
      </Tabs>
    </>
  );
}
