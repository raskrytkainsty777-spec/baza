import { useEffect, useState } from "react";
import { Badge, Button, Code, CopyButton, Group, MultiSelect, Paper, Select, Stack, Switch, Text, TextInput, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cabApi } from "../api";
import { dt, n } from "../../ui";

const err = (e: any) => notifications.show({ color: "red", message: e.message });

function StatusLine({ i }: { i: any }) {
  if (!i) return <Badge size="sm" color="gray" variant="light">не подключена</Badge>;
  return (
    <Group gap="xs">
      <Badge size="sm" color={i.status === "ok" ? "green" : i.status === "error" ? "red" : "yellow"} variant="light">{i.status === "ok" ? "подключена" : i.status === "error" ? "ошибка" : "настроена, ждёт первой отправки"}</Badge>
      {!i.enabled && <Badge size="sm" color="gray" variant="light">выключена</Badge>}
      <Text size="xs" c="dimmed">отправлено {n(i.sent)} · в очереди {n(i.pending)}{i.dead ? ` · не доставлено ${n(i.dead)}` : ""}{i.last_test_at ? ` · тест ${dt(i.last_test_at)}` : ""}</Text>
      {i.last_error && <Text size="xs" c="red">{i.last_error}</Text>}
    </Group>
  );
}

function Actions({ i, onTest, onToggle, onDelete }: any) {
  if (!i) return null;
  return (
    <Group gap="xs" mt="sm">
      <Button size="xs" variant="light" onClick={onTest}>Отправить тестовый лид</Button>
      <Button size="xs" variant="subtle" onClick={onToggle}>{i.enabled ? "Выключить" : "Включить"}</Button>
      <Button size="xs" variant="subtle" color="red" onClick={onDelete}>Удалить</Button>
    </Group>
  );
}

export default function Integrations() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["cab-integrations"], queryFn: () => cabApi("/integrations"), refetchInterval: 30_000 });
  const fields = useQuery({ queryKey: ["cab-int-fields"], queryFn: () => cabApi("/integrations/fields") });
  const ga = useQuery({ queryKey: ["cab-google-account"], queryFn: () => cabApi("/integrations/google-account") });
  const items: any[] = list.data?.items || [];
  const gs = items.find((i) => i.kind === "gsheets");
  const con = items.find((i) => i.kind === "connector");
  const bust = () => qc.invalidateQueries({ queryKey: ["cab-integrations"] });

  const [gUrl, setGUrl] = useState("");
  const [gSheet, setGSheet] = useState<string | null>(null);
  const [gCols, setGCols] = useState<string[]>([]);
  const [gHeader, setGHeader] = useState(true);
  const [gInfo, setGInfo] = useState<any>(null);
  const [cUrl, setCUrl] = useState("");
  const [cMethod, setCMethod] = useState("POST");
  const [cSecret, setCSecret] = useState("");
  useEffect(() => { if (gs) { setGUrl(gs.config.url || ""); setGSheet(gs.config.sheet || null); setGCols(gs.config.columns || []); setGHeader(gs.config.header ?? true); } else if (fields.data && !gCols.length) setGCols(fields.data.default); }, [gs, fields.data]);
  useEffect(() => { if (con) { setCUrl(con.config.url || ""); setCMethod(con.config.method || "POST"); } }, [con]);

  const gCheck = useMutation({ mutationFn: () => cabApi("/integrations/check", { method: "POST", body: { kind: "gsheets", config: { url: gUrl } } }), onSuccess: (r: any) => { setGInfo(r); if (!gSheet && r.sheets?.length) setGSheet(r.sheets[0]); notifications.show({ color: "green", message: `Таблица «${r.title}» доступна, листов: ${r.sheets.length}` }); }, onError: err });
  const gSave = useMutation({ mutationFn: () => cabApi("/integrations", { method: "POST", body: { kind: "gsheets", config: { url: gUrl, sheet: gSheet, columns: gCols, header: gHeader } } }), onSuccess: () => { bust(); notifications.show({ color: "green", message: "Google Таблица подключена — новые контакты будут дописываться строками" }); }, onError: err });
  const cCheck = useMutation({ mutationFn: () => cabApi("/integrations/check", { method: "POST", body: { kind: "connector", config: { url: cUrl, method: cMethod, secret: cSecret } } }), onSuccess: () => notifications.show({ color: "green", message: "Коннектор принял тестовый лид" }), onError: err });
  const cSave = useMutation({ mutationFn: () => cabApi("/integrations", { method: "POST", body: { kind: "connector", config: { url: cUrl, method: cMethod, secret: cSecret } } }), onSuccess: () => { bust(); setCSecret(""); notifications.show({ color: "green", message: "Коннектор подключён" }); }, onError: err });
  const test = useMutation({ mutationFn: (id: number) => cabApi(`/integrations/${id}/test`, { method: "POST" }), onSuccess: () => { bust(); notifications.show({ color: "green", message: "Тестовый лид 79999999999 отправлен" }); }, onError: (e: any) => { bust(); err(e); } });
  const toggle = useMutation({ mutationFn: (i: any) => cabApi(`/integrations/${i.id}?enabled=${!i.enabled}`, { method: "PATCH" }), onSuccess: bust, onError: err });
  const del = useMutation({ mutationFn: (id: number) => cabApi(`/integrations/${id}`, { method: "DELETE" }), onSuccess: () => { bust(); setGInfo(null); }, onError: err });
  const fieldOpts = (fields.data?.fields || []).map((f: any) => ({ value: f.key, label: f.label }));

  return (
    <>
      <Title order={2} mb={4}>Интеграции</Title>
      <Text c="dimmed" size="sm" mb="md">куда уходят купленные контакты · новые контакты доставляются в течение нескольких минут после появления в базе · повторы не отправляются</Text>
      <Stack>
        <Paper>
          <Group justify="space-between" mb="xs"><Text fw={600}>Google Таблицы</Text><StatusLine i={gs} /></Group>
          <Text size="sm">1. Откройте доступ <b>редактора</b> вашей таблице для аккаунта:</Text>
          <Group gap="xs" mt={4}>
            {ga.data?.name && ga.data?.email && <Text size="sm" fw={500}>{ga.data.name}</Text>}
            <Code>{ga.data?.email || ga.data?.error || "…"}</Code>
            {ga.data?.email && <CopyButton value={ga.data.email}>{({ copied, copy }) => <Button size="compact-xs" variant="light" onClick={copy}>{copied ? "скопировано" : "копировать"}</Button>}</CopyButton>}
          </Group>
          <Text size="sm" mt="sm">2. Вставьте ссылку на таблицу и проверьте доступ:</Text>
          <Group align="flex-end" mt={4}>
            <TextInput style={{ flex: 1 }} placeholder="https://docs.google.com/spreadsheets/d/…" value={gUrl} onChange={(e) => setGUrl(e.currentTarget.value)} />
            <Button variant="light" loading={gCheck.isPending} disabled={!gUrl.trim()} onClick={() => gCheck.mutate()}>Проверить</Button>
          </Group>
          {(gInfo || gs) && <>
            <Text size="sm" mt="sm">3. Лист и столбцы (порядок = порядок столбцов, начиная с A):</Text>
            <Group align="flex-end" mt={4}>
              <Select w={220} label="Лист" data={gInfo?.sheets || (gSheet ? [gSheet] : [])} value={gSheet} onChange={setGSheet} />
              <MultiSelect style={{ flex: 1 }} label="Столбцы по порядку" data={fieldOpts} value={gCols} onChange={setGCols} />
            </Group>
            <Switch mt="xs" label="Записать заголовки в первую строку, если она пустая" checked={gHeader} onChange={(e) => setGHeader(e.currentTarget.checked)} />
            {gInfo?.header?.length ? <Text size="xs" c="dimmed" mt={4}>сейчас в первой строке: {gInfo.header.join(" · ")}</Text> : null}
            <Button mt="sm" loading={gSave.isPending} disabled={!gUrl.trim() || !gSheet || !gCols.length} onClick={() => gSave.mutate()}>{gs ? "Сохранить изменения" : "Подключить"}</Button>
          </>}
          <Actions i={gs} onTest={() => test.mutate(gs.id)} onToggle={() => toggle.mutate(gs)} onDelete={() => del.mutate(gs.id)} />
        </Paper>

        <Paper>
          <Group justify="space-between" mb="xs"><Text fw={600}>Внешний коннектор — ваш URL</Text><StatusLine i={con} /></Group>
          <Text size="sm" c="dimmed">Каждый новый контакт уходит запросом на ваш адрес: POST с JSON или GET с параметрами. Поля: phone, operator, region, supplier, supplier_label, source_phone, company, bought_at, lf_status. Секрет, если задан, идёт в заголовке <Code>X-Baza-Secret</Code>.</Text>
          <Group align="flex-end" mt="xs">
            <TextInput style={{ flex: 1 }} label="URL" placeholder="https://ваш-сервис/leads" value={cUrl} onChange={(e) => setCUrl(e.currentTarget.value)} />
            <Select w={110} label="Метод" data={["POST", "GET"]} value={cMethod} onChange={(v) => setCMethod(v || "POST")} />
            <TextInput w={200} label="Секрет" placeholder={con?.config?.secret_set ? "задан" : "необязательно"} value={cSecret} onChange={(e) => setCSecret(e.currentTarget.value)} />
          </Group>
          <Group mt="sm" gap="xs">
            <Button variant="light" loading={cCheck.isPending} disabled={!cUrl.trim()} onClick={() => cCheck.mutate()}>Проверить тестовым лидом</Button>
            <Button loading={cSave.isPending} disabled={!cUrl.trim()} onClick={() => cSave.mutate()}>{con ? "Сохранить" : "Подключить"}</Button>
          </Group>
          <Actions i={con} onTest={() => test.mutate(con.id)} onToggle={() => toggle.mutate(con)} onDelete={() => del.mutate(con.id)} />
        </Paper>

        <Paper>
          <Group justify="space-between"><Text fw={600}>Bitrix24 · AmoCRM · Telegram</Text><Badge size="sm" color="gray" variant="light">следующий шаг</Badge></Group>
          <Text size="sm" c="dimmed" mt={4}>Лиды или сделки, ответственный, воронка и стадия, куда класть телефон и источник; для Amo — токен и поддомен; бот с вечерней сводкой.</Text>
        </Paper>
      </Stack>
    </>
  );
}
