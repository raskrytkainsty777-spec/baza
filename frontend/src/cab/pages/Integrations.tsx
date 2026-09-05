import { useEffect, useState } from "react";
import { Badge, Button, Code, CopyButton, Group, MultiSelect, Paper, PasswordInput, SegmentedControl, Select, Stack, Switch, Text, TextInput, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cabApi } from "../api";
import { dt, n } from "../../ui";

const err = (e: any) => notifications.show({ color: "red", message: e.message });
const grow = { flex: 1, minWidth: 220 } as const;
const opt = (xs: any[] | undefined) => (xs || []).map((x) => ({ value: String(x.id), label: x.name }));

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

function Actions({ i, onTest, onToggle, onDelete, testing }: any) {
  if (!i) return null;
  return (
    <Group gap="xs" mt="sm">
      <Button size="xs" variant="light" loading={testing} onClick={onTest}>Отправить тестовый лид</Button>
      <Button size="xs" variant="subtle" onClick={onToggle}>{i.enabled ? "Выключить" : "Включить"}</Button>
      <Button size="xs" variant="subtle" color="red" onClick={onDelete}>Удалить</Button>
    </Group>
  );
}

/** Общее для CRM: проверка → справочники → форма → сохранить. */
function useCrm(kind: string, existing: any, secretKeys: string[]) {
  const qc = useQueryClient();
  const [f, setF] = useState<any>({});
  const [refs, setRefs] = useState<any>(null);
  useEffect(() => { if (existing) setF({ ...existing.config, ...Object.fromEntries(secretKeys.map((k) => [k, ""])) }); }, [existing]);   // eslint-disable-line
  const check = useMutation({
    mutationFn: () => cabApi("/integrations/check", { method: "POST", body: { kind, config: f } }),
    onSuccess: (r: any) => { setRefs(r); notifications.show({ color: "green", message: kind === "bitrix" ? "Bitrix24 отвечает — выберите, куда класть контакты" : `AmoCRM «${r.account}» отвечает — выберите воронку и стадию` }); },
    onError: err,
  });
  useEffect(() => { if (existing && !refs && !check.isPending && !check.isError) check.mutate(); }, [existing]);   // eslint-disable-line
  const save = useMutation({
    mutationFn: () => cabApi("/integrations", { method: "POST", body: { kind, config: f } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["cab-integrations"] }); notifications.show({ color: "green", message: "Сохранено — новые контакты пойдут в CRM" }); },
    onError: err,
  });
  const set = (k: string, v: any) => setF((x: any) => ({ ...x, [k]: v }));
  return { f, set, refs, check, save };
}

function Bitrix({ i, actions }: { i: any; actions: any }) {
  const { f, set, refs, check, save } = useCrm("bitrix", i, ["webhook"]);
  const cat = (refs?.categories || []).find((c: any) => String(c.id) === String(f.category_id ?? 0));
  const canSave = (f.webhook || i?.config?.webhook_set) && refs;
  return (
    <Paper>
      <Group justify="space-between" mb="xs"><Text fw={600}>Bitrix24</Text><StatusLine i={i} /></Group>
      <Text size="sm" c="dimmed">Bitrix24 → Разработчикам → Другое → Входящий вебхук, права CRM (crm) и Пользователи (user). Контакт ищется по номеру, дубли не создаются.</Text>
      <Group align="flex-end" mt="xs">
        <PasswordInput style={grow} label="Входящий вебхук" placeholder={i?.config?.webhook_set ? `задан · ${i.config.webhook_host}` : "https://портал.bitrix24.ru/rest/1/xxxxxxxx/"} value={f.webhook || ""} onChange={(e) => set("webhook", e.currentTarget.value)} />
        <Button variant="light" loading={check.isPending} disabled={!f.webhook && !i?.config?.webhook_set} onClick={() => check.mutate()}>Проверить</Button>
      </Group>
      {refs && <>
        <Group mt="sm" align="flex-end">
          <div><Text size="sm" fw={500} mb={4}>Создавать</Text><SegmentedControl size="xs" data={[{ value: "lead", label: "Лид" }, { value: "deal", label: "Сделку + контакт" }]} value={f.entity || "lead"} onChange={(v) => set("entity", v)} /></div>
          {refs.users?.length ? <Select style={grow} label="Ответственный" data={opt(refs.users)} value={f.responsible_id ? String(f.responsible_id) : null} onChange={(v) => set("responsible_id", v)} searchable clearable /> : <TextInput style={grow} label="Ответственный — ID пользователя" description="список недоступен: у вебхука нет права «Пользователи»" value={f.responsible_id || ""} onChange={(e) => set("responsible_id", e.currentTarget.value)} />}
        </Group>
        <Group mt="xs" align="flex-end">
          {(f.entity || "lead") === "lead"
            ? <Select style={grow} label="Стадия лида" data={opt(refs.lead_statuses)} value={f.status_id || null} onChange={(v) => set("status_id", v)} clearable />
            : <>
              <Select style={grow} label="Воронка" data={opt(refs.categories)} value={String(f.category_id ?? 0)} onChange={(v) => { set("category_id", v); set("stage_id", ""); }} />
              <Select style={grow} label="Стадия сделки" data={opt(cat?.stages)} value={f.stage_id || null} onChange={(v) => set("stage_id", v)} clearable />
            </>}
          <Select w={170} label="Тип телефона" data={opt(refs.phone_types)} value={f.phone_type || "MOBILE"} onChange={(v) => set("phone_type", v)} />
          <Select style={grow} label="Источник (поле CRM)" data={opt(refs.sources)} value={f.source_id || null} onChange={(v) => set("source_id", v)} clearable />
        </Group>
        <Switch mt="xs" label="Не создавать, если номер уже есть в CRM" checked={f.dedupe ?? true} onChange={(e) => set("dedupe", e.currentTarget.checked)} />
        <Text size="xs" c="dimmed" mt={4}>В описание источника и комментарий пишем: номер-источник, компанию, поставщика, оператора, регион, дату.</Text>
        <Button mt="sm" loading={save.isPending} disabled={!canSave} onClick={() => save.mutate()}>{i ? "Сохранить изменения" : "Подключить"}</Button>
      </>}
      <Actions i={i} {...actions} />
    </Paper>
  );
}

function Amo({ i, actions }: { i: any; actions: any }) {
  const { f, set, refs, check, save } = useCrm("amo", i, ["token"]);
  const pl = (refs?.pipelines || []).find((p: any) => String(p.id) === String(f.pipeline_id));
  const canSave = (f.subdomain || i?.config?.subdomain) && (f.token || i?.config?.token_set) && refs;
  return (
    <Paper>
      <Group justify="space-between" mb="xs"><Text fw={600}>AmoCRM</Text><StatusLine i={i} /></Group>
      <Text size="sm" c="dimmed">amoCRM → Настройки → Интеграции → Создать интеграцию → «Долгосрочный токен». Контакт ищется по номеру, сделка создаётся на него, подробности — в примечании.</Text>
      <Group align="flex-end" mt="xs">
        <TextInput w={220} label="Поддомен" placeholder="mycompany" rightSection={<Text size="xs" c="dimmed" pr={4}>.amocrm.ru</Text>} rightSectionWidth={80} value={f.subdomain || ""} onChange={(e) => set("subdomain", e.currentTarget.value)} />
        <PasswordInput style={grow} label="Долгосрочный токен" placeholder={i?.config?.token_set ? "задан" : "eyJ0eXAiOiJKV1Qi…"} value={f.token || ""} onChange={(e) => set("token", e.currentTarget.value)} />
        <Button variant="light" loading={check.isPending} disabled={!(f.subdomain || i?.config?.subdomain) || !(f.token || i?.config?.token_set)} onClick={() => check.mutate()}>Проверить</Button>
      </Group>
      {refs && <>
        <Group mt="sm" align="flex-end">
          <Select style={grow} label="Воронка" data={opt(refs.pipelines)} value={f.pipeline_id ? String(f.pipeline_id) : null} onChange={(v) => { set("pipeline_id", v); set("status_id", null); }} />
          <Select style={grow} label="Этап" data={opt(pl?.statuses)} value={f.status_id ? String(f.status_id) : null} onChange={(v) => set("status_id", v)} clearable />
          <Select style={grow} label="Ответственный" data={opt(refs.users)} value={f.responsible_id ? String(f.responsible_id) : null} onChange={(v) => set("responsible_id", v)} searchable clearable />
        </Group>
        <Group mt="xs" align="flex-end">
          <Select w={170} label="Тип телефона" data={opt(refs.phone_types)} value={f.phone_type || "MOB"} onChange={(v) => set("phone_type", v)} />
          <TextInput w={200} label="Тег на сделке" value={f.tag ?? "ГЦК"} onChange={(e) => set("tag", e.currentTarget.value)} />
          <Switch label="Не создавать контакт, если номер уже есть" checked={f.dedupe ?? true} onChange={(e) => set("dedupe", e.currentTarget.checked)} />
        </Group>
        <Button mt="sm" loading={save.isPending} disabled={!canSave} onClick={() => save.mutate()}>{i ? "Сохранить изменения" : "Подключить"}</Button>
      </>}
      <Actions i={i} {...actions} />
    </Paper>
  );
}

export default function Integrations() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["cab-integrations"], queryFn: () => cabApi("/integrations"), refetchInterval: 30_000 });
  const fields = useQuery({ queryKey: ["cab-int-fields"], queryFn: () => cabApi("/integrations/fields") });
  const ga = useQuery({ queryKey: ["cab-google-account"], queryFn: () => cabApi("/integrations/google-account") });
  const items: any[] = list.data?.items || [];
  const by = (k: string) => items.find((i) => i.kind === k);
  const gs = by("gsheets"), con = by("connector");
  const bust = () => qc.invalidateQueries({ queryKey: ["cab-integrations"] });

  const [gUrl, setGUrl] = useState("");
  const [gSheet, setGSheet] = useState<string | null>(null);
  const [gCols, setGCols] = useState<string[]>([]);
  const [gHeader, setGHeader] = useState(true);
  const [gInfo, setGInfo] = useState<any>(null);
  const [cUrl, setCUrl] = useState("");
  const [cMethod, setCMethod] = useState("POST");
  const [cSecret, setCSecret] = useState("");
  useEffect(() => { if (gs) { setGUrl(gs.config.url || ""); setGSheet(gs.config.sheet || null); setGCols(gs.config.columns || []); setGHeader(gs.config.header ?? true); } else if (fields.data && !gCols.length) setGCols(fields.data.default); }, [gs, fields.data]);   // eslint-disable-line
  useEffect(() => { if (con) { setCUrl(con.config.url || ""); setCMethod(con.config.method || "POST"); } }, [con]);

  const gCheck = useMutation({ mutationFn: () => cabApi("/integrations/check", { method: "POST", body: { kind: "gsheets", config: { url: gUrl } } }), onSuccess: (r: any) => { setGInfo(r); if (!gSheet && r.sheets?.length) setGSheet(r.sheets[0]); notifications.show({ color: "green", message: `Таблица «${r.title}» доступна, листов: ${r.sheets.length}` }); }, onError: err });
  const gSave = useMutation({ mutationFn: () => cabApi("/integrations", { method: "POST", body: { kind: "gsheets", config: { url: gUrl, sheet: gSheet, columns: gCols, header: gHeader } } }), onSuccess: () => { bust(); notifications.show({ color: "green", message: "Google Таблица подключена — новые контакты будут дописываться строками" }); }, onError: err });
  const cCheck = useMutation({ mutationFn: () => cabApi("/integrations/check", { method: "POST", body: { kind: "connector", config: { url: cUrl, method: cMethod, secret: cSecret } } }), onSuccess: () => notifications.show({ color: "green", message: "Коннектор принял тестовый лид" }), onError: err });
  const cSave = useMutation({ mutationFn: () => cabApi("/integrations", { method: "POST", body: { kind: "connector", config: { url: cUrl, method: cMethod, secret: cSecret } } }), onSuccess: () => { bust(); setCSecret(""); notifications.show({ color: "green", message: "Коннектор подключён" }); }, onError: err });
  const test = useMutation({ mutationFn: (id: number) => cabApi(`/integrations/${id}/test`, { method: "POST" }), onSuccess: (r: any) => { bust(); notifications.show({ color: "green", message: `Тестовый лид 79999999999 отправлен${r.note ? ": " + r.note : ""}` }); }, onError: (e: any) => { bust(); err(e); } });
  const toggle = useMutation({ mutationFn: (i: any) => cabApi(`/integrations/${i.id}?enabled=${!i.enabled}`, { method: "PATCH" }), onSuccess: bust, onError: err });
  const del = useMutation({ mutationFn: (id: number) => cabApi(`/integrations/${id}`, { method: "DELETE" }), onSuccess: () => { bust(); setGInfo(null); }, onError: err });
  const fieldOpts = (fields.data?.fields || []).map((f: any) => ({ value: f.key, label: f.label }));
  const actions = (i: any) => ({ onTest: () => test.mutate(i.id), onToggle: () => toggle.mutate(i), onDelete: () => { if (confirm("Удалить интеграцию? Очередь доставки в неё будет очищена.")) del.mutate(i.id); }, testing: test.isPending && test.variables === i.id });

  return (
    <>
      <Title order={2} mb={4}>Интеграции</Title>
      <Text c="dimmed" size="sm" mb="md">куда уходят купленные контакты · новые контакты доставляются в течение нескольких минут после появления в базе · повторы не отправляются · тестовый лид — номер 79999999999, всё остальное «тест»</Text>
      <Stack>
        <Paper>
          <Group justify="space-between" mb="xs"><Text fw={600}>Google Таблицы</Text><StatusLine i={gs} /></Group>
          <Text size="sm">1. Откройте доступ <b>редактора</b> вашей таблице для аккаунта:</Text>
          <Group gap="xs" mt={4}>
            {ga.data?.name && ga.data?.email && <Text size="sm" fw={500}>{ga.data.name}</Text>}
            {ga.data && !ga.data.email ? <Text size="sm" c="red">{ga.data.error || "Google-аккаунт у нас пока не настроен — напишите администратору"}</Text> : <Code>{ga.data?.email || "…"}</Code>}
            {ga.data?.email && <CopyButton value={ga.data.email}>{({ copied, copy }) => <Button size="compact-xs" variant="light" onClick={copy}>{copied ? "скопировано" : "копировать"}</Button>}</CopyButton>}
          </Group>
          <Text size="sm" mt="sm">2. Вставьте ссылку на таблицу и проверьте доступ:</Text>
          <Group align="flex-end" mt={4}>
            <TextInput style={grow} placeholder="https://docs.google.com/spreadsheets/d/…" value={gUrl} onChange={(e) => setGUrl(e.currentTarget.value)} />
            <Button variant="light" loading={gCheck.isPending} disabled={!gUrl.trim()} onClick={() => gCheck.mutate()}>Проверить</Button>
          </Group>
          {(gInfo || gs) && <>
            <Text size="sm" mt="sm">3. Лист и столбцы (порядок = порядок столбцов, начиная с A):</Text>
            <Group align="flex-end" mt={4}>
              <Select w={220} label="Лист" data={gInfo?.sheets || (gSheet ? [gSheet] : [])} value={gSheet} onChange={setGSheet} />
              <MultiSelect style={grow} label="Столбцы по порядку" data={fieldOpts} value={gCols} onChange={setGCols} />
            </Group>
            <Switch mt="xs" label="Записать заголовки в первую строку, если она пустая" checked={gHeader} onChange={(e) => setGHeader(e.currentTarget.checked)} />
            {gInfo?.header?.length ? <Text size="xs" c="dimmed" mt={4}>сейчас в первой строке: {gInfo.header.join(" · ")}</Text> : null}
            <Button mt="sm" loading={gSave.isPending} disabled={!gUrl.trim() || !gSheet || !gCols.length} onClick={() => gSave.mutate()}>{gs ? "Сохранить изменения" : "Подключить"}</Button>
          </>}
          <Actions i={gs} {...(gs ? actions(gs) : {})} />
        </Paper>

        <Bitrix i={by("bitrix")} actions={by("bitrix") ? actions(by("bitrix")) : {}} />
        <Amo i={by("amo")} actions={by("amo") ? actions(by("amo")) : {}} />

        <Paper>
          <Group justify="space-between" mb="xs"><Text fw={600}>Внешний коннектор — ваш URL</Text><StatusLine i={con} /></Group>
          <Text size="sm" c="dimmed">Каждый новый контакт уходит запросом на ваш адрес: POST с JSON или GET с параметрами. Поля: phone, operator, region, supplier, supplier_label, source_phone, company, bought_at, lf_status. Секрет, если задан, идёт в заголовке <Code>X-Baza-Secret</Code>.</Text>
          <Group align="flex-end" mt="xs">
            <TextInput style={grow} label="URL" placeholder="https://ваш-сервис/leads" value={cUrl} onChange={(e) => setCUrl(e.currentTarget.value)} />
            <Select w={110} label="Метод" data={["POST", "GET"]} value={cMethod} onChange={(v) => setCMethod(v || "POST")} />
            <TextInput w={200} label="Секрет" placeholder={con?.config?.secret_set ? "задан" : "необязательно"} value={cSecret} onChange={(e) => setCSecret(e.currentTarget.value)} />
          </Group>
          <Group mt="sm" gap="xs">
            <Button variant="light" loading={cCheck.isPending} disabled={!cUrl.trim()} onClick={() => cCheck.mutate()}>Проверить тестовым лидом</Button>
            <Button loading={cSave.isPending} disabled={!cUrl.trim()} onClick={() => cSave.mutate()}>{con ? "Сохранить" : "Подключить"}</Button>
          </Group>
          <Actions i={con} {...(con ? actions(con) : {})} />
        </Paper>
      </Stack>
    </>
  );
}
