import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { ActionIcon, Badge, Button, Center, Container, Group, Modal, Paper, PasswordInput, Select, Stack, Table, Text, TextInput, Title, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconArrowLeft, IconExternalLink, IconLogout, IconPlus, IconWallet } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { money, n } from "../ui";

const KEY = "agent_token";
const tok = () => localStorage.getItem(KEY) || "";
async function agentApi<T = any>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const res = await fetch(`/api/agent${path}`, { method: opts.method || "GET", headers: { "Content-Type": "application/json", ...(tok() ? { Authorization: `Bearer ${tok()}` } : {}) }, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
  if (res.status === 401 && !path.startsWith("/auth/")) { localStorage.removeItem(KEY); location.assign("/agent/login"); }
  if (!res.ok) { let d = res.statusText; try { const j = await res.json(); d = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j); } catch {} throw new Error(d); }
  return res.json();
}
const err = (e: any) => notifications.show({ color: "red", message: e.message });
const useMe = () => useQuery({ queryKey: ["agent-me"], queryFn: () => agentApi("/me"), refetchInterval: 60_000 });

function Login() {
  const nav = useNavigate();
  const [login, setLogin] = useState(""); const [password, setPassword] = useState(""); const [e, setE] = useState(""); const [busy, setBusy] = useState(false);
  const submit = async (ev: React.FormEvent) => { ev.preventDefault(); setBusy(true); setE(""); try { const r = await agentApi("/auth/login", { method: "POST", body: { login, password } }); localStorage.setItem(KEY, r.token); nav("/agent", { replace: true }); } catch (ex: any) { setE(ex.message); } finally { setBusy(false); } };
  return (
    <Center h="100vh" bg="gray.0"><Paper w={360} shadow="sm"><form onSubmit={submit}><Stack gap="md">
      <div><Title order={3}>Досбор</Title><Text size="sm" c="dimmed">Вход для агентов</Text></div>
      <TextInput label="Логин" value={login} onChange={(ev) => setLogin(ev.currentTarget.value)} autoFocus />
      <PasswordInput label="Пароль" value={password} onChange={(ev) => setPassword(ev.currentTarget.value)} error={e || undefined} />
      <Button type="submit" loading={busy} disabled={!login || !password}>Войти</Button>
    </Stack></form></Paper></Center>
  );
}

function Wallet({ me }: { me: any }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [f, setF] = useState<any>({ kind: "sbp", bank: "", value: "" });
  useEffect(() => { if (me?.requisites) setF(me.requisites); }, [me]);
  const save = useMutation({ mutationFn: () => agentApi("/requisites", { method: "PATCH", body: f }), onSuccess: () => { setOpen(false); qc.invalidateQueries({ queryKey: ["agent-me"] }); notifications.show({ color: "green", message: "Реквизиты сохранены" }); }, onError: err });
  return (
    <>
      <Tooltip label={me?.requisites ? "реквизиты для выплаты" : "добавьте реквизиты — без них источники не принимаются"}>
        <ActionIcon variant={me?.requisites ? "light" : "filled"} color={me?.requisites ? "teal" : "red"} size="lg" onClick={() => setOpen(true)}><IconWallet size={18} /></ActionIcon>
      </Tooltip>
      <Modal opened={open} onClose={() => setOpen(false)} title="Реквизиты для выплаты">
        <Select label="Способ" data={[{ value: "sbp", label: "СБП по номеру телефона" }, { value: "card", label: "Карта" }]} value={f.kind} onChange={(v) => setF({ ...f, kind: v || "sbp" })} />
        <TextInput label="Банк" value={f.bank} onChange={(e) => setF({ ...f, bank: e.currentTarget.value })} mt="xs" />
        <TextInput label={f.kind === "sbp" ? "Номер телефона" : "Номер карты"} value={f.value} onChange={(e) => setF({ ...f, value: e.currentTarget.value })} mt="xs" />
        <Button fullWidth mt="md" loading={save.isPending} disabled={!f.bank || !f.value} onClick={() => save.mutate()}>Сохранить</Button>
      </Modal>
    </>
  );
}

function Header({ me }: { me: any }) {
  const nav = useNavigate();
  return (
    <Group justify="space-between" mb="md">
      <div><Text fw={600}>{me?.name}</Text><Text size="xs" c="dimmed">{me?.client_name}</Text></div>
      <Group gap="sm">
        <Badge size="lg" variant="light" color="teal" style={{ textTransform: "none" }}>баланс: {money(me?.balance)}</Badge>
        <Wallet me={me} />
        <ActionIcon variant="subtle" color="gray" size="lg" onClick={() => { localStorage.removeItem(KEY); nav("/agent/login"); }}><IconLogout size={18} /></ActionIcon>
      </Group>
    </Group>
  );
}

function TaskList() {
  const me = useMe();
  const nav = useNavigate();
  const tasks: any[] = me.data?.tasks || [];
  return (
    <Container size="sm" py="md">
      <Header me={me.data} />
      <Title order={3} mb="sm">Задачи</Title>
      <Stack>
        {tasks.map((t) => (
          <Paper key={t.id} p="sm" style={{ cursor: "pointer" }} onClick={() => nav(`/agent/task/${t.id}`)}>
            <Group justify="space-between"><div><Text fw={600}>{t.name}</Text><Text size="xs" c="dimmed">{money(t.price_per_source)} за уникальный источник · сайтов {n(t.resources)}</Text></div>
              <div style={{ textAlign: "right" }}><Text fw={700} className="num">{n(t.found)}{t.limit_sources ? ` / ${n(t.limit_sources)}` : ""}</Text><Text size="xs" c="dimmed">моих {n(t.mine)}{t.left != null ? ` · осталось ${n(t.left)}` : ""}</Text></div></Group>
          </Paper>
        ))}
        {!tasks.length && <Paper><Text c="dimmed" ta="center">активных задач нет</Text></Paper>}
      </Stack>
    </Container>
  );
}

function TaskView() {
  const { id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const me = useMe();
  const task = useQuery({ queryKey: ["agent-task", id], queryFn: () => agentApi(`/tasks/${id}`), refetchInterval: 30_000 });
  const [mode, setMode] = useState<"menu" | "resources">("menu");
  const res = useQuery({ queryKey: ["agent-res", id], queryFn: () => agentApi(`/tasks/${id}/resources`), enabled: mode === "resources" });
  const comps = useQuery({ queryKey: ["agent-comps", id], queryFn: () => agentApi(`/tasks/${id}/companies`) });
  const mine = useQuery({ queryKey: ["agent-my", id], queryFn: () => agentApi(`/tasks/${id}/my`) });
  const [open, setOpen] = useState(false);
  const [company, setCompany] = useState<string | null>(null);
  const [phone, setPhone] = useState("");
  const [check, setCheck] = useState<any>(null);
  const digits = phone.replace(/\D/g, "");
  const full = digits.length === 10 ? "7" + digits : digits;
  const doCheck = useMutation({ mutationFn: () => agentApi(`/tasks/${id}/check?phone=${full}`), onSuccess: setCheck, onError: err });
  const add = useMutation({
    mutationFn: () => agentApi(`/tasks/${id}/sources`, { method: "POST", body: { company_id: Number(company), phone: full } }),
    onSuccess: (r: any) => { setOpen(false); setPhone(""); setCheck(null); qc.invalidateQueries({ queryKey: ["agent-me"] }); qc.invalidateQueries({ queryKey: ["agent-task", id] }); qc.invalidateQueries({ queryKey: ["agent-my", id] }); notifications.show({ color: "green", message: `Источник ${r.phone} добавлен${r.purchased ? " и отправлен в закупку" : ""}. Баланс ${money(r.balance)}` }); },
    onError: err,
  });
  const t = task.data;
  const noReq = me.data && !me.data.requisites;
  const compOpts = (comps.data?.items || []).map((c: any) => ({ value: String(c.id), label: c.name }));
  return (
    <Container size="sm" py="md">
      <Header me={me.data} />
      <Group justify="space-between" mb="sm">
        <Group gap="xs"><ActionIcon variant="subtle" onClick={() => (mode === "resources" ? setMode("menu") : nav("/agent"))}><IconArrowLeft size={18} /></ActionIcon><div><Title order={3}>{t?.name}</Title><Text size="xs" c="dimmed">{money(t?.price_per_source)} за источник · найдено {n(t?.found)}{t?.limit_sources ? ` из ${n(t.limit_sources)}` : ""}{t?.left === 0 ? " · лимит исчерпан" : ""}</Text></div></Group>
      </Group>
      {mode === "menu" && (
        <Stack>
          <Button size="lg" variant="light" onClick={() => setMode("resources")}>Получить ресурсы · {n(t?.resources)} сайтов</Button>
          <Button size="lg" leftSection={<IconPlus size={18} />} disabled={t?.left === 0} onClick={() => { if (noReq) { notifications.show({ color: "red", message: "Сначала добавьте реквизиты для выплаты (иконка кошелька)" }); return; } setOpen(true); }}>Добавить источник</Button>
          <Paper p="sm"><Text fw={600} size="sm" mb="xs">Мои источники в этой задаче · {n(t?.mine)}</Text>
            <Table fz="xs"><Table.Tbody>{(mine.data?.items || []).slice(0, 50).map((s: any) => <Table.Tr key={s.id}><Table.Td className="mono">{s.phone}</Table.Td><Table.Td>{s.company}</Table.Td></Table.Tr>)}</Table.Tbody></Table>
          </Paper>
        </Stack>
      )}
      {mode === "resources" && (
        <Paper p="xs">
          <Table fz="sm"><Table.Tbody>
            {(res.data?.items || []).map((r: any) => <Table.Tr key={r.n}><Table.Td className="num" w={40}>{r.n}</Table.Td><Table.Td><a href={r.url} target="_blank" rel="noreferrer" className="rowlink">{r.url} <IconExternalLink size={12} /></a></Table.Td><Table.Td><Text size="xs" c="dimmed">{r.company}</Text></Table.Td></Table.Tr>)}
          </Table.Tbody></Table>
          <Button mt="sm" variant="light" leftSection={<IconArrowLeft size={16} />} onClick={() => setMode("menu")}>Назад</Button>
        </Paper>
      )}
      <Modal opened={open} onClose={() => setOpen(false)} title="Новый источник">
        <Select label="Компания" description="только из списка задачи" data={compOpts} value={company} onChange={setCompany} searchable nothingFoundMessage="такой компании нет в задаче" />
        <TextInput label="Номер телефона" placeholder="+7 900 000-00-00" value={phone} onChange={(e) => { setPhone(e.currentTarget.value); setCheck(null); }} mt="xs" leftSection={<Text size="sm">+7</Text>} />
        {check && <Text size="sm" c={check.ok ? "green" : "red"} mt="xs">{check.ok ? `Уникальный источник ${check.phone} — можно добавлять` : check.reason}</Text>}
        <Group mt="md" grow>
          <Button variant="light" loading={doCheck.isPending} disabled={!company || full.length !== 11} onClick={() => doCheck.mutate()}>Проверить</Button>
          <Button loading={add.isPending} disabled={!check?.ok} onClick={() => add.mutate()}>Добавить</Button>
        </Group>
      </Modal>
    </Container>
  );
}

function Require({ children }: { children: JSX.Element }) { return tok() ? children : <Navigate to="/agent/login" replace />; }

export default function AgentApp() {
  return (
    <Routes>
      <Route path="login" element={<Login />} />
      <Route path="task/:id" element={<Require><TaskView /></Require>} />
      <Route path="*" element={<Require><TaskList /></Require>} />
    </Routes>
  );
}
