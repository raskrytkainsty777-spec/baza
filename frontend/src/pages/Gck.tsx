import { useEffect, useState } from "react";
import { Badge, Button, Code, Group, Menu, Modal, NumberInput, Paper, PasswordInput, Table, Text, TextInput, Textarea, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconDotsVertical, IconPlus } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { dt, n } from "../ui";

export default function Gck() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["gck-clients"], queryFn: () => api("/gck/clients"), refetchInterval: 30_000 });
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api("/settings") });
  const [open, setOpen] = useState(false);
  const [f, setF] = useState<any>({ login: "", password: "", name: "", lf_crm_id: "", answer_cost: "", limit_default: "" });
  const [g, setG] = useState<any>({});
  useEffect(() => { if (settings.data) setG({ gck_answer_cost: settings.data.values.gck_answer_cost, gck_limit_default: settings.data.values.gck_limit_default, cab_telegram_bot_token: settings.data.values.cab_telegram_bot_token || "", google_sa_json: settings.data.values.google_sa_json || "", google_sa_name: settings.data.values.google_sa_name || "" }); }, [settings.data]);
  const err = (e: any) => notifications.show({ color: "red", message: e.message });
  const ginfo = useQuery({ queryKey: ["gck-google"], queryFn: () => api("/gck/google/info") });
  const gcheck = useMutation({ mutationFn: () => api("/gck/google/check", { method: "POST", body: { json_key: g.google_sa_json } }), onSuccess: (r: any) => notifications.show({ color: "green", message: `Ключ рабочий: ${r.client_email}` }), onError: err });
  const bust = () => qc.invalidateQueries({ queryKey: ["gck-clients"] });
  const create = useMutation({
    mutationFn: () => api("/gck/clients", { method: "POST", body: { login: f.login, password: f.password, name: f.name, lf_crm_id: f.lf_crm_id ? Number(f.lf_crm_id) : null, answer_cost: f.answer_cost ? Number(f.answer_cost) : null, limit_default: f.limit_default ? Number(f.limit_default) : null } }),
    onSuccess: (r: any) => { setOpen(false); setF({ login: "", password: "", name: "", lf_crm_id: "", answer_cost: "", limit_default: "" }); bust(); notifications.show({ color: "green", message: `Клиент создан. ${(r.steps || []).join("; ")}` }); },
    onError: err,
  });
  const saveG = useMutation({
    mutationFn: () => api("/settings", { method: "PUT", body: { values: g } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["settings"] }); notifications.show({ color: "green", message: "Сохранено" }); }, onError: err,
  });
  const act = useMutation({
    mutationFn: ({ id, path, body }: any) => api(`/gck/clients/${id}${path}`, { method: body === undefined ? "POST" : "PATCH", body }),
    onSuccess: bust, onError: err,
  });
  const openCab = async (id: number) => {
    try { const r = await api(`/gck/clients/${id}/session`, { method: "POST" }); window.open(`/cabinet?token=${encodeURIComponent(r.token)}`, "_blank"); } catch (e: any) { err(e); }
  };
  const items: any[] = list.data?.items || [];

  return (
    <>
      <Group justify="space-between" mb="md">
        <div><Title order={2}>ГЦК — генерация целевых клиентов</Title><Text c="dimmed" size="sm">клиент = проект Leads Factory · баланс в контактах = остаток ₽ / цена заявки · поступления вносятся в ЛК LF руками, сюда подтягиваются сами</Text></div>
        <Button leftSection={<IconPlus size={16} />} onClick={() => setOpen(true)}>Создать клиента</Button>
      </Group>

      <Paper mb="md">
        <Group align="flex-end" gap="sm">
          <NumberInput label="Цена заявки LF по умолчанию, ₽" w={220} value={Number(g.gck_answer_cost || 5)} onChange={(v) => setG({ ...g, gck_answer_cost: String(v) })} min={0} decimalScale={2} />
          <NumberInput label="Лимит на связку по умолчанию" w={220} value={Number(g.gck_limit_default || 5)} onChange={(v) => setG({ ...g, gck_limit_default: String(v) })} min={0} />
          <PasswordInput label="Токен Telegram-бота клиентов" w={320} value={g.cab_telegram_bot_token || ""} onChange={(e) => setG({ ...g, cab_telegram_bot_token: e.currentTarget.value })} className="mono" />
          <Button variant="light" loading={saveG.isPending} onClick={() => saveG.mutate()}>Сохранить</Button>
          <Text size="xs" c="dimmed">токен Leads Factory — в Настройках → Ключи и связки</Text>
        </Group>
        <Group align="flex-end" gap="sm" mt="md">
          <TextInput w={260} label="Google — название аккаунта" description="так подпишем его клиенту" placeholder="Робот ГЦК" value={g.google_sa_name || ""} onChange={(e) => setG({ ...g, google_sa_name: e.currentTarget.value })} />
          <Textarea style={{ flex: 1 }} label="JSON ключ сервисного аккаунта" description="один на всех клиентов; его почта показывается клиенту в интеграции Google Таблиц" autosize minRows={2} maxRows={6} className="mono" value={g.google_sa_json || ""} onChange={(e) => setG({ ...g, google_sa_json: e.currentTarget.value })} placeholder='{"type": "service_account", "client_email": "...", "private_key": "..."}' />
          <Button variant="light" loading={gcheck.isPending} disabled={!(g.google_sa_json || "").trim()} onClick={() => gcheck.mutate()}>Проверить ключ</Button>
          <Button variant="light" loading={saveG.isPending} onClick={() => { saveG.mutate(); setTimeout(() => qc.invalidateQueries({ queryKey: ["gck-google"] }), 800); }}>Сохранить</Button>
        </Group>
        <Text size="xs" c="dimmed" mt={4}>Сейчас сохранён: {ginfo.data?.email ? <>{ginfo.data.name ? `${ginfo.data.name} · ` : ""}<Code>{ginfo.data.email}</Code></> : ginfo.data?.error ? <Text span c="red">{ginfo.data.error}</Text> : "ключа нет"}</Text>
      </Paper>

      <Paper p="xs" style={{ overflowX: "auto" }}>
        <Table fz="xs" verticalSpacing={5} className="compact">
          <Table.Thead><Table.Tr><Table.Th>Клиент</Table.Th><Table.Th>Проект LF</Table.Th><Table.Th>Закупка</Table.Th><Table.Th ta="right">Баланс, контактов</Table.Th><Table.Th ta="right">₽</Table.Th><Table.Th ta="right">Цена</Table.Th><Table.Th ta="right">Источников</Table.Th><Table.Th ta="right">Куплено всего</Table.Th><Table.Th ta="right">Сегодня</Table.Th><Table.Th>Синхронизация</Table.Th><Table.Th /></Table.Tr></Table.Thead>
          <Table.Tbody>
            {items.map((c) => (
              <Table.Tr key={c.id} style={{ opacity: c.is_active ? 1 : 0.5 }}>
                <Table.Td><Text size="sm" fw={500}>{c.name}</Text><Text size="xs" c="dimmed" className="mono">{c.login}</Text></Table.Td>
                <Table.Td className="num">{c.lf_crm_id || "—"}</Table.Td>
                <Table.Td><Badge size="xs" variant="light" color={c.lf_status === "active" ? "green" : c.lf_status === "pause" ? "yellow" : "gray"}>{c.lf_status || "—"}</Badge>{c.lf_error && <Text size="xs" c="red" className="clip" style={{ maxWidth: 220 }} title={c.lf_error}>{c.lf_error}</Text>}</Table.Td>
                <Table.Td className="num" ta="right"><Text span fw={600} c={c.balance_contacts ? undefined : "red"}>{c.balance_contacts == null ? "—" : n(c.balance_contacts)}</Text></Table.Td>
                <Table.Td className="num" ta="right">{c.lf_balance_rub == null ? "—" : n(c.lf_balance_rub)}</Table.Td>
                <Table.Td className="num" ta="right">{c.lf_answer_cost ?? "—"}</Table.Td>
                <Table.Td className="num" ta="right">{n(c.sources)} <span className="muted">/ вкл {n(c.sources_on)}</span></Table.Td>
                <Table.Td className="num" ta="right">{n(c.contacts_total)}</Table.Td>
                <Table.Td className="num" ta="right">{n(c.contacts_today)}</Table.Td>
                <Table.Td><Text size="xs" c="dimmed">баланс {dt(c.balance_synced_at)}<br />контакты {dt(c.contacts_synced_at)}</Text></Table.Td>
                <Table.Td>
                  <Group gap={4} wrap="nowrap">
                    <Button size="compact-xs" variant="light" onClick={() => openCab(c.id)}>Кабинет</Button>
                    <Menu shadow="md" width={230} position="bottom-end">
                      <Menu.Target><Button size="compact-xs" variant="subtle" color="gray"><IconDotsVertical size={14} /></Button></Menu.Target>
                      <Menu.Dropdown>
                        <Menu.Item onClick={() => act.mutate({ id: c.id, path: "/refresh" })}>Обновить баланс из LF</Menu.Item>
                        <Menu.Label>Статус закупки LF</Menu.Label>
                        <Menu.Item onClick={() => act.mutate({ id: c.id, path: "/lf-status?status=active" })}>Активный</Menu.Item>
                        <Menu.Item onClick={() => act.mutate({ id: c.id, path: "/lf-status?status=pause" })}>Пауза</Menu.Item>
                        <Menu.Item onClick={() => act.mutate({ id: c.id, path: "/lf-status?status=stop" })}>Стоп</Menu.Item>
                        <Menu.Label>Клиент</Menu.Label>
                        <Menu.Item onClick={() => { const p = prompt("Новый пароль (от 6 символов)"); if (p) act.mutate({ id: c.id, path: "", body: { password: p } }); }}>Сменить пароль</Menu.Item>
                        <Menu.Item color={c.is_active ? "red" : "green"} onClick={() => act.mutate({ id: c.id, path: "", body: { is_active: !c.is_active } })}>{c.is_active ? "Отключить" : "Включить"}</Menu.Item>
                      </Menu.Dropdown>
                    </Menu>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
            {!items.length && <Table.Tr><Table.Td colSpan={11}><Text c="dimmed" ta="center">клиентов пока нет</Text></Table.Td></Table.Tr>}
          </Table.Tbody>
        </Table>
      </Paper>

      <Modal opened={open} onClose={() => setOpen(false)} title="Новый клиент" size="lg">
        <Group grow>
          <TextInput label="Логин" value={f.login} onChange={(e) => setF({ ...f, login: e.currentTarget.value })} />
          <PasswordInput label="Пароль" description="от 6 символов, выдаёте клиенту" value={f.password} onChange={(e) => setF({ ...f, password: e.currentTarget.value })} />
        </Group>
        <TextInput label="Название" description="так назовём проект в Leads Factory" value={f.name} onChange={(e) => setF({ ...f, name: e.currentTarget.value })} mt="xs" />
        <Group grow mt="xs">
          <TextInput label="Существующий проект LF (crm_id)" description="пусто — создадим новый" value={f.lf_crm_id} onChange={(e) => setF({ ...f, lf_crm_id: e.currentTarget.value })} />
          <TextInput label="Цена заявки, ₽" description="пусто — по умолчанию" value={f.answer_cost} onChange={(e) => setF({ ...f, answer_cost: e.currentTarget.value })} />
          <TextInput label="Лимит на связку" description="пусто — по умолчанию" value={f.limit_default} onChange={(e) => setF({ ...f, limit_default: e.currentTarget.value })} />
        </Group>
        <Text size="xs" c="dimmed" mt="sm">В LF сразу ставим: цену заявки, мин. остаток 0, лимит по умолчанию, автоповышение выкл. Закупка включится сама, когда появятся источники и баланс. Поступление внесите в ЛК LF руками.</Text>
        <Button fullWidth mt="md" loading={create.isPending} disabled={!f.login || f.password.length < 6 || !f.name} onClick={() => create.mutate()}>Создать</Button>
      </Modal>
    </>
  );
}
