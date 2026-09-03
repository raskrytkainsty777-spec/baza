import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Alert, Button, Code, Group, NumberInput, Paper, PasswordInput, Select, Stack, Switch, Table, Text, TextInput, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconPlus } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { City, Kpi, KpiRow, n, useCities } from "../ui";

export function Cities() {
  const qc = useQueryClient();
  const cities = useCities();
  const [name, setName] = useState("");
  const add = useMutation({
    mutationFn: () => api("/cities", { method: "POST", body: { name } }),
    onSuccess: () => { setName(""); qc.invalidateQueries({ queryKey: ["cities"] }); },
    onError: (e: any) => notifications.show({ color: "red", message: e.message }),
  });
  const rows = (cities.data?.cities || []).filter((c) => c.is_active || c.donors_new + c.donors_monitored + c.donors_paused > 0);
  const hidden = (cities.data?.cities.length || 0) - rows.length;

  return (
    <>
      <Group justify="space-between" mb="md">
        <div><Title order={2}>Города</Title><Text c="dimmed" size="sm">город = проект · неразобранных доноров: {cities.data?.unclassified_donors ?? "…"}</Text></div>
        <Group gap="xs">
          <TextInput size="sm" placeholder="Название города" value={name} onChange={(e) => setName(e.currentTarget.value)} w={220} />
          <Button leftSection={<IconPlus size={16} />} loading={add.isPending} disabled={!name.trim()} onClick={() => add.mutate()}>Добавить</Button>
        </Group>
      </Group>
      <Paper style={{ overflowX: "auto" }}>
        <Table>
          <Table.Thead><Table.Tr>
            <Table.Th>Город</Table.Th><Table.Th>Доноров<br /><span className="muted" style={{ textTransform: "none", fontWeight: 400 }}>новые / монитор / пауза</span></Table.Th>
            <Table.Th>Постов</Table.Th><Table.Th>Продающих</Table.Th><Table.Th>В сборе</Table.Th><Table.Th>Лидов</Table.Th><Table.Th>Непробитых</Table.Th><Table.Th>Пробив</Table.Th><Table.Th>CRM</Table.Th>
          </Table.Tr></Table.Thead>
          <Table.Tbody>
            {rows.map((c) => (
              <Table.Tr key={c.id}>
                <Table.Td><Link className="rowlink" to={`/cities/${c.id}`}>{c.name}</Link>{!c.is_active && <Text span size="xs" c="dimmed"> выкл</Text>}</Table.Td>
                <Table.Td className="num">{c.donors_new} / {c.donors_monitored} / {c.donors_paused}</Table.Td>
                <Table.Td className="num">{n(c.posts)}</Table.Td><Table.Td className="num">{n(c.posts_selling)}</Table.Td><Table.Td className="num">{n(c.posts_active)}</Table.Td>
                <Table.Td className="num">{n(c.leads)}</Table.Td><Table.Td className="num">{n(c.leads_unprobed)}</Table.Td>
                <Table.Td><Text size="xs" c={c.probe_enabled ? "green.8" : "dimmed"}>{c.probe_enabled ? `вкл · ${c.probe_mode === "auto" ? "авто" : "вручную"}` : "выкл"}</Text></Table.Td>
                <Table.Td><Text size="xs" c={c.crm_webhook_url ? "green.8" : "dimmed"}>{c.crm_webhook_url ? "настроен" : "—"}</Text></Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        {hidden > 0 && <Text size="xs" c="dimmed" mt="xs">ещё {hidden} городов из вашего файла выключены и пусты — появятся здесь, когда в них попадёт донор</Text>}
      </Paper>
    </>
  );
}

export function CityPage() {
  const { id } = useParams();
  const qc = useQueryClient();
  const city = useQuery({ queryKey: ["city", id], queryFn: () => api<City & Record<string, any>>(`/cities/${id}`) });
  const [f, setF] = useState<any>({});
  useEffect(() => { if (city.data) setF({ ...city.data, probe_hook_token: "", crm_secret: "" }); }, [city.data]);
  const save = useMutation({
    mutationFn: () => {
      const body: any = {};
      for (const k of ["name", "is_active", "cost_per_contact", "cost_per_handling", "comment_fresh_days", "post_freeze_days", "donor_pause_days", "resend_after_days", "probe_mode", "probe_enabled", "crm_webhook_url", "send_mode"]) body[k] = f[k];
      if (f.probe_hook_token) body.probe_hook_token = f.probe_hook_token;
      if (f.crm_secret) body.crm_secret = f.crm_secret;
      return api(`/cities/${id}`, { method: "PATCH", body });
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["city", id] }); qc.invalidateQueries({ queryKey: ["cities"] }); notifications.show({ color: "green", message: "Сохранено" }); },
    onError: (e: any) => notifications.show({ color: "red", message: e.message }),
  });
  const c = city.data;
  if (!c) return <Text c="dimmed">загрузка…</Text>;
  const set = (k: string, v: any) => setF((s: any) => ({ ...s, [k]: v }));
  const base = location.origin;

  return (
    <>
      <Group justify="space-between" mb="md">
        <div><Title order={2}>{c.name}</Title><Text c="dimmed" size="sm"><Link to="/cities" className="rowlink">Города</Link> › {c.name} · {c.is_active ? "активен" : "выключен"}</Text></div>
        <Button loading={save.isPending} onClick={() => save.mutate()}>Сохранить</Button>
      </Group>

      <KpiRow>
        <Kpi value={`${c.donors_new} / ${c.donors_monitored}`} label="доноров: новых / на мониторе" hint={`на паузе ${c.donors_paused}`} />
        <Kpi value={n(c.posts)} label="постов" hint={`продающих ${n(c.posts_selling)} · в сборе ${n(c.posts_active)}`} />
        <Kpi value={n(c.leads)} label="лидов всего" />
        <Kpi value={<Text span c={c.leads_unprobed ? "orange.8" : undefined}>{n(c.leads_unprobed)}</Text>} label="непробитых" />
        <Kpi value={n(c.leads_with_phone)} label="с номером" hint={`отправлено в CRM ${n(c.leads_sent)}`} />
      </KpiRow>

      <Alert color="violet" variant="light" mb="md" title="Отсеки «непробитые / пробитые»">
        Выбор актуальности по датам, ручная отдача в пробив и автоматическая выдача в CRM появятся здесь, когда заработает сбор комментариев и первые лиды лягут в базу. Настройки ниже уже действуют.
      </Alert>

      <Group align="flex-start" grow>
        <Stack>
          <Paper>
            <Text fw={600} mb="xs">Город</Text>
            <TextInput label="Название" value={f.name || ""} onChange={(e) => set("name", e.currentTarget.value)} mb="xs" />
            <Switch label="Активен" checked={!!f.is_active} onChange={(e) => set("is_active", e.currentTarget.checked)} />
          </Paper>
          <Paper>
            <Text fw={600} mb="xs">Цены</Text>
            <Group grow>
              <NumberInput label="Запрос пробива, ₽" value={f.cost_per_contact ?? 0} onChange={(v) => set("cost_per_contact", v)} min={0} decimalScale={2} />
              <NumberInput label="Обработка контакта, ₽" value={f.cost_per_handling ?? 0} onChange={(v) => set("cost_per_handling", v)} min={0} decimalScale={2} />
            </Group>
            <Text size="xs" c="dimmed" mt="xs">Кладутся снимком в каждый новый лид — смена цен не переписывает историю.</Text>
          </Paper>
          <Paper>
            <Text fw={600} mb="xs">Правила</Text>
            <Group grow>
              <NumberInput label="Комментарии не старше, дн" value={f.comment_fresh_days} onChange={(v) => set("comment_fresh_days", v)} min={1} />
              <NumberInput label="Заморозка поста без прироста, дн" value={f.post_freeze_days} onChange={(v) => set("post_freeze_days", v)} min={1} />
            </Group>
            <Group grow mt="xs">
              <NumberInput label="Пауза донора без постов, дн" value={f.donor_pause_days} onChange={(v) => set("donor_pause_days", v)} min={1} />
              <NumberInput label="Не отдавать того же человека раньше, дн" value={f.resend_after_days} onChange={(v) => set("resend_after_days", v)} min={0} />
            </Group>
          </Paper>
        </Stack>
        <Stack>
          <Paper>
            <Text fw={600} mb="xs">Пробив</Text>
            <Group grow>
              <Select label="Режим" value={f.probe_mode || "manual"} onChange={(v) => set("probe_mode", v)} data={[{ value: "manual", label: "вручную — по датам" }, { value: "auto", label: "авто — всех новых сразу" }]} />
              <Switch label="Пробив включён" mt={28} checked={!!f.probe_enabled} onChange={(e) => set("probe_enabled", e.currentTarget.checked)} />
            </Group>
            <PasswordInput label="Токен задачи на сервисе пробива" placeholder={c.probe_hook_token_set ? "задан — введите, чтобы заменить" : "из вкладки Задачи → 🔗 на старом сервере"} value={f.probe_hook_token || ""} onChange={(e) => set("probe_hook_token", e.currentTarget.value)} mt="xs" />
            <Text size="xs" c="dimmed" mt="xs">Постбек с номером сервис пробива шлёт на <Code>{base}/api/probe/callback</Code></Text>
          </Paper>
          <Paper>
            <Text fw={600} mb="xs">CRM — куда уходят пробитые</Text>
            <TextInput label="Адрес приёма лидов" placeholder="https://ваш-сервис/leads" value={f.crm_webhook_url || ""} onChange={(e) => set("crm_webhook_url", e.currentTarget.value)} />
            <Group grow mt="xs">
              <PasswordInput label="Секрет (X-Hook-Secret)" placeholder={c.crm_secret_set ? "задан" : "придумайте"} value={f.crm_secret || ""} onChange={(e) => set("crm_secret", e.currentTarget.value)} />
              <Select label="Отдавать" value={f.send_mode || "auto"} onChange={(v) => set("send_mode", v)} data={[{ value: "auto", label: "автоматически, сразу" }, { value: "manual", label: "вручную" }]} />
            </Group>
            <Text size="xs" c="dimmed" mt="xs">Статусы (негатив / заявка / квал / сделка) CRM шлёт по lead_id на <Code>{base}/api/crm/status</Code></Text>
          </Paper>
        </Stack>
      </Group>
    </>
  );
}
