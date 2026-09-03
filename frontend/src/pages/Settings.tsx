import { useEffect, useState } from "react";
import { Badge, Button, Group, NumberInput, Paper, Stack, Table, Text, TextInput, Textarea, Title } from "@mantine/core";
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

export default function Settings() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api("/settings") });
  const [v, setV] = useState<Record<string, string>>({});
  useEffect(() => { if (q.data) setV(q.data.values); }, [q.data]);
  const save = useMutation({
    mutationFn: () => api("/settings", { method: "PUT", body: { values: v } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["settings"] }); notifications.show({ color: "green", message: "Сохранено" }); },
    onError: (e: any) => notifications.show({ color: "red", message: e.message }),
  });
  const set = (k: string, val: any) => setV((s) => ({ ...s, [k]: String(val ?? "") }));
  const env = q.data?.env;
  const flag = (ok: boolean) => <Badge size="xs" variant="light" color={ok ? "green" : "red"}>{ok ? "задан" : "нет"}</Badge>;

  return (
    <>
      <Group justify="space-between" mb="md">
        <div><Title order={2}>Настройки</Title><Text c="dimmed" size="sm">общее для всех городов · цены, правила и связки — в каждом городе</Text></div>
        <Button loading={save.isPending} onClick={() => save.mutate()}>Сохранить</Button>
      </Group>

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
          </Paper>
          <Paper>
            <Text fw={600} mb="xs">Сбор</Text>
            <Group grow>
              <NumberInput label="Окно постов при заводе донора, дн" value={Number(v["intake_days"] || 45)} onChange={(x) => set("intake_days", x)} min={1} />
              <NumberInput label="Свежесть комментариев по умолчанию, дн" value={Number(v["comment_fresh_days_default"] || 30)} onChange={(x) => set("comment_fresh_days_default", x)} min={1} />
            </Group>
            <Group grow mt="xs">
              <NumberInput label="Строк в тарифе parser.im" value={Number(v["parserim_lines"] || 10)} onChange={(x) => set("parserim_lines", x)} min={1} />
              <NumberInput label="Крупный пост — от комментариев" description="досбор через Apify, а не parser.im" value={Number(v["big_post_threshold"] || 1000)} onChange={(x) => set("big_post_threshold", x)} min={100} />
              <NumberInput label="Потолок Apify, $/день" value={Number(v["apify_daily_cap_usd"] || 10)} onChange={(x) => set("apify_daily_cap_usd", x)} min={0} />
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
            <Text size="xs" c="dimmed" mt="xs">Порядок утром важен: новые посты → прирост → досбор, иначе досбор не увидит прироста.</Text>
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
