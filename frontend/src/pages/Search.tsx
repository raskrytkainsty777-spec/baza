import { useState } from "react";
import { Badge, Button, Group, Modal, NumberInput, Paper, Select, SimpleGrid, Stack, Table, Text, TextInput, Textarea, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconArrowRight, IconDownload, IconReportAnalytics } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getToken, qs } from "../api";
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
      {cell(1, t.kind === "apify_keyword" ? "фильтр активности" : "фильтр f1", `${n(t.passed)} прошли · ${n(t.rejected_inactive)} ${t.kind === "apify_keyword" ? "отсеяны" : "неактивны"}`)}<IconArrowRight size={14} color="gray" />
      {cell(2, "ИИ: кто и где", `${n(t.confident)} уверенно · ${n(t.unclear)} неясно · ${n(t.rejected_activity)} не те`)}<IconArrowRight size={14} color="gray" />
      {cell(3, "готово", t.stage === "distributed" ? `распределено ${n(t.distributed)}` : "к распределению")}
    </Group>
  );
}

function Report({ id, onClose }: { id: number | null; onClose: () => void }) {
  const r = useQuery({ queryKey: ["search-report", id], queryFn: () => api(`/search/tasks/${id}/report`), enabled: id != null });
  const d: any = r.data;
  const mini = (rows: any[], head: string[], cells: (x: any) => any[]) => (
    <Table fz="xs" verticalSpacing={3}>
      <Table.Thead><Table.Tr>{head.map((h, i) => <Table.Th key={h} ta={i ? "right" : "left"}>{h}</Table.Th>)}</Table.Tr></Table.Thead>
      <Table.Tbody>{rows.map((x, k) => <Table.Tr key={k}>{cells(x).map((v, i) => <Table.Td key={i} className={i ? "num" : undefined}>{v}</Table.Td>)}</Table.Tr>)}
        {!rows.length && <Table.Tr><Table.Td colSpan={head.length}><Text c="dimmed" size="xs">пусто</Text></Table.Td></Table.Tr>}</Table.Tbody>
    </Table>
  );
  return (
    <Modal opened={id != null} onClose={onClose} size="xl" title={d ? `Итог: ${d.task.title}` : "Итог"}>
      {d && (
        <Stack gap="md">
          <Text size="sm" c="dimmed">собрано {n(d.task.collected)} → прошли f1 {n(d.task.passed)} → уверенно {n(d.task.confident)} · неясно {n(d.task.unclear)} · не те {n(d.task.rejected_activity)} → доноров {n(d.task.distributed)}</Text>
          <SimpleGrid cols={2}>
            <div><Text fw={600} size="sm" mb={4}>По этапам</Text>{mini(d.states, ["этап", "кандидатов"], (x) => [x.label, n(x.count)])}
              {!!d.reject_reasons.length && <><Text fw={600} size="sm" mt="sm" mb={4}>Почему отклонены</Text>{mini(d.reject_reasons, ["причина", "шт."], (x) => [x.label, n(x.count)])}</>}</div>
            <div><Text fw={600} size="sm" mb={4}>По городам</Text>{mini(d.cities, ["город", "доноров", "ждут распределения", "неясно"], (x) => [x.city, n(x.distributed), n(x.waiting), n(x.unclear)])}</div>
          </SimpleGrid>
          <div><Text fw={600} size="sm" mb={4}>{d.task.kind === "recommendation" ? "Что дал каждый сид" : d.task.kind === "mentions" ? "Кто упомянул" : d.task.kind === "followings" ? "У кого в подписках" : "Что дал каждый тег / ключ"}</Text>
            {mini(d.sources, ["источник", "собрано", "прошли f1", "стали донорами"], (x) => [x.source, n(x.collected), n(x.passed), n(x.distributed)])}</div>
        </Stack>
      )}
    </Modal>
  );
}

export default function Search() {
  const qc = useQueryClient();
  const cities = useCities();
  const [kind, setKind] = useState("apify_keyword");
  const [text, setText] = useState("");
  const [lastDays, setLastDays] = useState<number | string>(7);
  const [minComm, setMinComm] = useState<number | string>(20);
  const [mentionCity, setMentionCity] = useState("");
  const [minDonors, setMinDonors] = useState<number | string>(2);
  const [perAccount, setPerAccount] = useState<number | string>(1500);
  const avail = useQuery({ queryKey: ["followings-available", mentionCity], queryFn: () => api(`/search/followings/available${qs({ city_id: mentionCity || "" })}`), enabled: kind === "followings" });
  const [assignCity, setAssignCity] = useState("");
  const [tid, setTid] = useState("");
  const [tidLines, setTidLines] = useState<number | string>(1);
  const [sel, setSel] = useState<number[]>([]);
  const [reportId, setReportId] = useState<number | null>(null);
  const downloadCsv = async (id: number) => {
    const res = await fetch(`/api/search/tasks/${id}/export.csv`, { headers: { Authorization: `Bearer ${getToken()}` } });
    if (!res.ok) { notifications.show({ color: "red", message: `Не удалось скачать: ${res.status}` }); return; }
    const blob = await res.blob(); const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `search_task_${id}.csv`; a.click();
  };

  const tasks = useQuery({ queryKey: ["search-tasks"], queryFn: () => api("/search/tasks"), refetchInterval: 20_000 });
  const unclear = useQuery({ queryKey: ["candidates", "unclear"], queryFn: () => api(`/search/candidates${qs({ state: "unclear", limit: 200 })}`) });
  const bust = () => { qc.invalidateQueries({ queryKey: ["search-tasks"] }); qc.invalidateQueries({ queryKey: ["candidates"] }); qc.invalidateQueries({ queryKey: ["donors"] }); };
  const err = (e: any) => notifications.show({ color: "red", message: e.message });

  const create = useMutation({
    mutationFn: () => api("/search/tasks", { method: "POST", body: { kind, values: text.split(kind === "keyword" ? /[\n,]+/ : /\n+/).map((s) => s.trim()).filter(Boolean), lastpost_days: Number(lastDays) || 7, min_comments: Number(minComm) || 0, city_id: (kind === "mentions" || kind === "followings") && mentionCity ? Number(mentionCity) : null, min_donors: Number(minDonors) || 1, per_account: Number(perAccount) || 1500 } }),
    onSuccess: () => { setText(""); bust(); notifications.show({ color: "green", message: kind === "apify_keyword" ? "Задача создана — Apify ищет, обычно 1–3 минуты" : kind === "mentions" ? "Задача создана — упоминания собраны из базы, дальше f1 → ИИ" : kind === "followings" ? "Задача создана — подписки собирает parser.im, это небыстро" : "Задача создана — сбор начнётся, когда освободятся строки parser.im" }); }, onError: err,
  });
  const adopt = useMutation({
    mutationFn: () => api("/search/adopt", { method: "POST", body: { tid: tid.trim(), lines: Number(tidLines) || 1 } }),
    onSuccess: (t: any) => { setTid(""); bust(); notifications.show({ color: "green", message: `Подключено: ${t.title}` }); }, onError: err,
  });
  const distribute = useMutation({ mutationFn: (id: number) => api(`/search/tasks/${id}/distribute`, { method: "POST" }), onSuccess: (r: any) => { bust(); notifications.show({ color: "green", message: `В города ушло ${r.distributed}` }); }, onError: err });
  const assign = useMutation({ mutationFn: () => api("/search/candidates/assign", { method: "POST", body: { candidate_ids: sel, city_id: assignCity ? Number(assignCity) : null } }), onSuccess: () => { setSel([]); bust(); }, onError: err });
  const reject = useMutation({ mutationFn: () => api("/search/candidates/reject", { method: "POST", body: { candidate_ids: sel } }), onSuccess: () => { setSel([]); bust(); }, onError: err });

  const items: any[] = tasks.data?.items || [];
  const unc: any[] = unclear.data?.items || [];

  return (
    <>
      <Group justify="space-between" mb="md">
        <div><Title order={2}>Поиск доноров</Title><Text c="dimmed" size="sm">задачи идут сами: сбор → фильтр f1 → ИИ «кто и где» → уверенные (≥80%) сразу становятся донорами своих городов. Неясные — ниже, ждут вас.</Text></div>
      </Group>

      <Paper mb="md">
        <Text fw={600} mb="xs">Новая задача</Text>
        <Group align="flex-start" gap="xs">
          <Select w={230} value={kind} onChange={(v) => setKind(v || "hashtag")} data={[{ value: "apify_keyword", label: "по ключам · Apify" }, { value: "hashtag", label: "по тегам · parser.im" }, { value: "keyword", label: "по ключам · parser.im" }, { value: "mentions", label: "упоминания у доноров" }, { value: "followings", label: "подписки доноров с лидами" }]} />
          {kind === "mentions" || kind === "followings" ? (
            <Select style={{ flex: 1 }} placeholder="город доноров (пусто — все)" clearable value={mentionCity} onChange={(v) => setMentionCity(v || "")} data={cityOptions(cities.data?.cities, false)} />
          ) : (
          <Textarea style={{ flex: 1 }} autosize minRows={3} placeholder={kind === "hashtag" ? "каждый тег с новой строки:\n#риелтормосква\n#новостройкимосквы" : kind === "apify_keyword" ? "каждый ключ с новой строки, как в поиске Instagram:\nриелтор спб\nнедвижимость петербург\nновостройки спб" : "ключи через запятую или с новой строки:\nриелтор, агент по недвижимости\nновостройки"} value={text} onChange={(e) => setText(e.currentTarget.value)} />
          )}
          <Button loading={create.isPending} disabled={kind !== "mentions" && kind !== "followings" && !text.trim()} onClick={() => create.mutate()}>Запустить</Button>
        </Group>
        {kind === "mentions" && <Text size="xs" c="dimmed" mt="xs">Берём @упоминания из подписей постов доноров города и из их описаний профиля: агентства, коллеги, партнёры. Уже известные и отклонённые не попадают. Дальше f1 → ИИ «кто и где».</Text>}
        {kind === "followings" && (
          <Group gap="xs" mt="xs" align="flex-end">
            <NumberInput w={190} size="xs" label="в подписках хотя бы у" description="доноров, от" min={1} value={minDonors} onChange={setMinDonors} />
            <NumberInput w={170} size="xs" label="подписок с донора" description="не больше" min={50} step={100} value={perAccount} onChange={setPerAccount} />
            <Text size="xs" c="dimmed">
              {avail.data ? <>доступно <b>{n(avail.data.available)}</b> доноров с лидами, у которых подписки ещё не собирали · уже собрано у {n(avail.data.collected)}</> : "…"}
              <br />parser.im p1 «подписки», по 10 логинов на задание · результат: логины, которые встречаются у нескольких доноров → f1 → ИИ «кто и где»
            </Text>
          </Group>
        )}
        {kind === "apify_keyword" && (
          <Group gap="xs" mt="xs" align="flex-end">
            <NumberInput w={170} size="xs" label="последний пост не старше" description="дней" min={1} value={lastDays} onChange={setLastDays} />
            <NumberInput w={190} size="xs" label="комментариев на лучшем посте" description="из 12 последних, от" min={0} value={minComm} onChange={setMinComm} />
            <Text size="xs" c="dimmed">Apify ищет профили по слову, как поиск в приложении, до 250 на ключ · ~2,3 $ за 1000 · без parser.im: активные сразу к ИИ «кто и где»</Text>
          </Group>
        )}
        <Group gap="xs" mt="sm" align="flex-end">
          <TextInput w={200} size="xs" label="Задание уже создано на сайте parser.im?" placeholder="tid, например 5531333" value={tid} onChange={(e) => setTid(e.currentTarget.value)} />
          <NumberInput w={150} size="xs" label="строк в нём" description="тегов или ключей" min={1} value={tidLines} onChange={setTidLines} />
          <Button size="xs" variant="light" loading={adopt.isPending} disabled={!tid.trim()} onClick={() => adopt.mutate()}>Подключить</Button>
          <Text size="xs" c="dimmed">p3 по тегам или p5 по ключам · без пересбора: результат заберём, когда задание закончится, дальше f1 → ИИ</Text>
        </Group>
        <Text size="xs" c="dimmed" mt="xs">Рекомендации Apify — из «Доноров»: отметьте сидов галочками и нажмите «Выбранных → в рекомендации». Уже известные аккаунты и отклонённые в задачу не попадают.</Text>
      </Paper>

      <Stack mb="md">
        {items.map((t) => (
          <Paper key={t.id}>
            <Group justify="space-between" mb="xs">
              <Group gap="xs"><Text fw={600}>{t.title}</Text><Badge size="xs" variant="light" color={t.kind === "recommendation" || t.kind === "apify_keyword" ? "cyan" : t.kind === "mentions" ? "teal" : "grape"}>{t.kind === "recommendation" || t.kind === "apify_keyword" ? "Apify" : t.kind === "mentions" ? "из базы" : t.kind === "followings" ? "parser.im · подписки" : "parser.im"}</Badge><StatusBadge kind="stage" value={t.stage} /><Text size="xs" c="dimmed">{dt(t.created_at)}</Text></Group>
              <Group gap={6}>
                {t.stage === "ready" && t.confident > 0 && <Button size="xs" loading={distribute.isPending} onClick={() => distribute.mutate(t.id)}>Распределить по городам · {n(t.confident)}</Button>}
                <Button size="xs" variant="light" leftSection={<IconReportAnalytics size={14} />} onClick={() => setReportId(t.id)}>Итог</Button>
                <Button size="xs" variant="subtle" leftSection={<IconDownload size={14} />} onClick={() => downloadCsv(t.id)}>CSV</Button>
              </Group>
            </Group>
            <Stages t={t} />
            {t.error && <Text size="xs" c="red" mt="xs">{t.error}</Text>}
          </Paper>
        ))}
        {!items.length && <Paper><Text c="dimmed" ta="center">задач пока нет</Text></Paper>}
      </Stack>

      <Report id={reportId} onClose={() => setReportId(null)} />

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
