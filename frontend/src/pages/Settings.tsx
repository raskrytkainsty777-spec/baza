import { useEffect, useState } from "react";
import { Badge, Button, Group, NumberInput, Paper, PasswordInput, Stack, Switch, Table, Text, TextInput, Textarea, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";

const PROMPTS: [string, string, string][] = [
  ["prompt.activity", "Кто нужен: отсев по деятельности", "риелторы, застройщики, агентства новостроек — берём; ипотечные брокеры, стройка домов, ремонт, дизайн — нет"],
  ["prompt.city", "Определение города", "по имени + описанию + адресу → город и уверенность"],
  ["prompt.post", "Разбор поста", "продающий? оффер, крючок, категория, тип призыва, кодовое слово"],
  ["prompt.post_city", "Город по посту", "для неразобранных доноров: какому городу принадлежит продающий пост"],
  ["prompt.comment", "Квалификация комментария", "комментарий вместе с постом → лид / мусор + суть"],
];
const SCHEDULE: [string, string, string][] = [
  ["schedule.new_posts", "новые посты доноров на мониторе", "Apify"],
  ["schedule.counters", "прирост счётчиков", "Apify"],
  ["schedule.comments", "досбор комментов по приросту", "parser.im / Apify"],
  ["schedule.rollup", "пересчёт витрины и правил", "своё"],
];
const WORKER_LABEL: Record<string, string> = {
  job_runner: "очередь parser.im", discovery: "поиск доноров", donor_intake: "заведение донора",
  comments_collect: "сбор комментариев", posts_sync: "Apify: посты и счётчики", ai_posts: "ИИ: посты",
  ai_comments: "ИИ: комментарии", probe_feeder: "подача на пробив", outbox: "отправка наружу", inbox: "приём статусов",
};

export default function Settings() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api("/settings") });
  const ops = useQuery({ queryKey: ["ops-status"], queryFn: () => api("/ops/status"), refetchInterval: 10_000 });
  const [v, setV] = useState<Record<string, string>>({});
  useEffect(() => { if (q.data) setV(q.data.values); }, [q.data]);
  const err = (e: any) => notifications.show({ color: "red", message: e.message });
  const save = useMutation({
    mutationFn: () => {
      const body: Record<string, string> = {};
      for (const [k, val] of Object.entries(v)) if (!/^(heartbeat|last_run|run_now|ai_cost)\./.test(k)) body[k] = val;
      return api("/settings", { method: "PUT", body: { values: body } });
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["settings"] }); notifications.show({ color: "green", message: "Сохранено" }); },
    onError: err,
  });
  const sw = useMutation({
    mutationFn: (body: any) => api("/ops/switches", { method: "PUT", body }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["ops-status"] }); qc.invalidateQueries({ queryKey: ["settings"] }); },
    onError: err,
  });
  const runNow = useMutation({
    mutationFn: (name: string) => api(`/ops/run/${name}`, { method: "POST" }),
    onSuccess: () => notifications.show({ color: "green", message: "Поставлено — воркер подхватит в течение минуты" }),
    onError: err,
  });
  const set = (k: string, val: any) => setV((s) => ({ ...s, [k]: String(val ?? "") }));
  const env = q.data?.env;
  const o = ops.data;
  const flag = (ok: boolean) => <Badge size="xs" variant="light" color={ok ? "green" : "red"}>{ok ? "задан" : "нет"}</Badge>;

  return (
    <>
      <Group justify="space-between" mb="md">
        <div><Title order={2}>Настройки</Title><Text c="dimmed" size="sm">общее для всех городов · цены, правила и связки — в каждом городе</Text></div>
        <Button loading={save.isPending} onClick={() => save.mutate()}>Сохранить</Button>
      </Group>

      <Paper mb="md">
        <Group align="flex-start" grow>
          <Stack gap="xs">
            <Text fw={600}>Управление</Text>
            <Switch label="Посты и заведение доноров" description="главный выключатель; где именно собирать — флаги в карточке города" checked={!!o?.collection_enabled} disabled={!o} onChange={(e) => sw.mutate({ collection_enabled: e.currentTarget.checked })} />
            <Switch label="Сбор комментариев" description="главный выключатель; включается в карточке города отдельно" checked={!!o?.comments_enabled} disabled={!o} onChange={(e) => sw.mutate({ comments_enabled: e.currentTarget.checked })} />
            <Switch label="ИИ включена" description="разметка постов, квалификация комментариев, «кто и где» по кандидатам" checked={!!o?.ai_enabled} disabled={!o} onChange={(e) => sw.mutate({ ai_enabled: e.currentTarget.checked })} />
            <Text size="xs" c="dimmed">Выключатель не трогает запущенные задания — они дойдут до конца и лягут в базу. Новые не создаются, очередь стоит. Поиск доноров и ИИ-разметка уже собранного работают всегда.</Text>
          </Stack>
          <Stack gap="xs">
            <Text fw={600}>Сейчас</Text>
            {o && <>
              <Text size="sm">parser.im: <span className="mono">{o.parserim.lines_busy} / {o.parserim.lines_total}</span> строк занято · в очереди заданий {o.parserim.queued}</Text>
              <Text size="sm">ИИ сегодня: <span className="mono">${Number(o.ai_cost_today_usd).toFixed(3)}</span></Text>
              <Group gap="xs">
                <Button size="xs" variant="light" loading={runNow.isPending} onClick={() => runNow.mutate("new_posts")}>Новые посты сейчас</Button>
                <Button size="xs" variant="light" loading={runNow.isPending} onClick={() => runNow.mutate("counters")}>Счётчики сейчас</Button>
              </Group>
              <Text size="xs" c="dimmed">последний обход: посты {o.last_run.new_posts || "—"} · счётчики {o.last_run.counters || "—"}</Text>
            </>}
          </Stack>
          <Stack gap={6}>
            <Text fw={600}>Воркеры</Text>
            <Group gap={4}>
              {o && Object.entries(o.workers).map(([k, w]: [string, any]) => (
                <Badge key={k} size="xs" variant="light" color={w.alive ? "green" : "red"} title={w.last || "не запускался"}>{WORKER_LABEL[k] || k}</Badge>
              ))}
            </Group>
            <Text size="xs" c="dimmed">зелёный — отметился за последние 5 минут</Text>
          </Stack>
        </Group>
      </Paper>

      <Group align="flex-start" grow>
        <Stack>
          <Paper>
            <Text fw={600} mb="xs">Ключи и связки <Text span size="xs" c="dimmed">— живут в .env на сервере, здесь только статус</Text></Text>
            {env && <Table>
              <Table.Tbody>
                <Table.Tr><Table.Td>parser.im</Table.Td><Table.Td>{flag(env.parserim_key_set)}</Table.Td></Table.Tr>
                <Table.Tr><Table.Td>Apify</Table.Td><Table.Td>{flag(env.apify_token_set)}</Table.Td></Table.Tr>
                <Table.Tr><Table.Td>OpenRouter</Table.Td><Table.Td>{flag(env.openrouter_key_set)} <Text span size="xs" c="dimmed" className="mono">{env.ai_model_cheap}</Text></Table.Td></Table.Tr>
                <Table.Tr><Table.Td>Telegram-бот оповещений</Table.Td><Table.Td>{flag(env.telegram_bot_set)} <Text span size="xs" c="dimmed">нажмите /start у бота — чат запомнится</Text></Table.Td></Table.Tr>
                <Table.Tr><Table.Td>Сервис пробива</Table.Td><Table.Td className="mono">{env.probe_base_url}</Table.Td></Table.Tr>
              </Table.Tbody>
            </Table>}
            <PasswordInput mt="sm" label="Leads Factory — токен Open API" description="выдаётся в их кабинете: Open API → Выдать токен. Хранится здесь, не в .env" placeholder="вставьте и нажмите Сохранить" value={v["leadsfactory_token"] || ""} onChange={(e) => set("leadsfactory_token", e.currentTarget.value)} className="mono" />
          </Paper>
          <Paper>
            <Text fw={600} mb="xs">Модели ИИ <Text span size="xs" c="dimmed">— id из каталога OpenRouter; замер 04.09 в docs/DECISIONS.md</Text></Text>
            <Group grow>
              <TextInput label="Комментарии" description="пачкой по посту; gemini-2.5-flash-lite: 98% согласия с haiku, $0.025/1000" value={v["ai_model.comments"] || ""} onChange={(e) => set("ai_model.comments", e.currentTarget.value)} className="mono" />
              <TextInput label="Посты" description="разметка продающих" value={v["ai_model.posts"] || ""} onChange={(e) => set("ai_model.posts", e.currentTarget.value)} className="mono" />
            </Group>
            <Group grow mt="xs">
              <TextInput label="Кандидаты: кто и где" description="объём небольшой, ошибка в городе дорогая — haiku" value={v["ai_model.cands"] || ""} onChange={(e) => set("ai_model.cands", e.currentTarget.value)} className="mono" />
              <NumberInput label="Комментариев в одном вызове" description="5–40; больше — дешевле, но длиннее ответ" value={Number(v["ai_batch_size"] || 20)} onChange={(x) => set("ai_batch_size", x)} min={5} max={40} />
            </Group>
            <Text size="xs" c="dimmed" mt="xs">До ИИ работают правила без вызова модели: голый «+», кодовое слово поста, одни эмодзи, одно @упоминание.</Text>
          </Paper>
          <Paper>
            <Text fw={600} mb="xs">Сбор</Text>
            <Group grow>
              <NumberInput label="Окно постов при заводе донора, дн" value={Number(v["intake_days"] || 45)} onChange={(x) => set("intake_days", x)} min={1} />
              <NumberInput label="Свежесть комментариев по умолчанию, дн" value={Number(v["comment_fresh_days_default"] || 30)} onChange={(x) => set("comment_fresh_days_default", x)} min={1} />
            </Group>
            <Group grow mt="xs">
              <NumberInput label="Строк в тарифе parser.im" value={Number(v["parserim_lines"] || 10)} onChange={(x) => set("parserim_lines", x)} min={1} />
              <NumberInput label="Первый сбор — от комментариев" description="мельче пропускаем; вырастет — возьмёт прирост" value={Number(v["min_comments_first"] || 1)} onChange={(x) => set("min_comments_first", x)} min={1} />
              <NumberInput label="Крупный пост — от комментариев" description="досбор через Apify, а не parser.im" value={Number(v["big_post_threshold"] || 1000)} onChange={(x) => set("big_post_threshold", x)} min={100} />
              <NumberInput label="Потолок Apify, $/день" value={Number(v["apify_daily_cap_usd"] || 10)} onChange={(x) => set("apify_daily_cap_usd", x)} min={0} />
            </Group>
            <Switch mt="sm" label="Неразобранные доноры: собирать посты" description="у них нет города и нет флага города; ИИ ставит город каждому продающему посту. Выключено — ждут" checked={(v["unclassified_collect_posts"] ?? "0") === "1"} onChange={(e) => set("unclassified_collect_posts", e.currentTarget.checked ? "1" : "0")} />
            <Switch mt="sm" label="Уверенных кандидатов распределять по городам автоматически" description="≥ 90% уверенности в городе и подходящая деятельность → донор города сразу, без кнопки; неясные ждут оператора" checked={(v["auto_distribute"] ?? "1") === "1"} onChange={(e) => set("auto_distribute", e.currentTarget.checked ? "1" : "0")} />
            <Group grow mt="xs">
              <NumberInput label="Фильтр f1: последний пост не старше, дн" description="кто не проходит — «неактивен», в базу отклонённых" value={Number(v["f1_lastpost_days"] || 30)} onChange={(x) => set("f1_lastpost_days", x)} min={1} />
              <NumberInput label="Фильтр f1: подписчиков от" description="0 — без порога" value={Number(v["f1_followers_min"] || 0)} onChange={(x) => set("f1_followers_min", x)} min={0} />
              <NumberInput label="Фильтр f1: подписчиков до" description="0 — без порога" value={Number(v["f1_followers_max"] || 0)} onChange={(x) => set("f1_followers_max", x)} min={0} />
            </Group>
            <Text size="xs" c="dimmed" mt="xs">Сбор комментариев всегда parser.im <span className="mono">web=1</span>, без dop и фильтров: 318 комм/мин против 3.6.</Text>
          </Paper>
          <Paper>
            <Text fw={600} mb="xs">Расписание <Text span size="xs" c="dimmed">— время МСК, через запятую если несколько</Text></Text>
            <Table>
              <Table.Tbody>
                {SCHEDULE.map(([k, label, who]) => (
                  <Table.Tr key={k}><Table.Td>{label}</Table.Td><Table.Td><Text size="xs" c="dimmed">{who}</Text></Table.Td>
                    <Table.Td><TextInput size="xs" w={140} value={v[k] || ""} onChange={(e) => set(k, e.currentTarget.value)} /></Table.Td></Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            <Text size="xs" c="dimmed" mt="xs">Порядок утром важен: новые посты → прирост → досбор, иначе досбор не увидит прироста. Досбор по приросту стартует сам, как только счётчики обновились.</Text>
          </Paper>
        </Stack>
        <Stack>
          <Paper>
            <Text fw={600} mb="xs">Промпты ИИ <Text span size="xs" c="dimmed">— правятся здесь, не в коде</Text></Text>
            {PROMPTS.map(([k, label, hint]) => (
              <Textarea key={k} label={label} description={hint} autosize minRows={2} maxRows={10} mb="sm" value={v[k] || ""} onChange={(e) => set(k, e.currentTarget.value)} placeholder="пусто — воркер возьмёт встроенный промпт по умолчанию" />
            ))}
          </Paper>
        </Stack>
      </Group>
    </>
  );
}
