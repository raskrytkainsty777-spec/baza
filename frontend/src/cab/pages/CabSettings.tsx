import { useEffect, useState } from "react";
import { Anchor, Badge, Button, Checkbox, Code, Group, NumberInput, Paper, SimpleGrid, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cabApi } from "../api";
import { useMe } from "../CabinetApp";

export default function CabSettings() {
  const qc = useQueryClient();
  const me = useMe();
  const suppliers = useQuery({ queryKey: ["cab-suppliers"], queryFn: () => cabApi("/suppliers") });
  const [f, setF] = useState<any>({});
  useEffect(() => { if (me.data) setF({ contact_cost: me.data.contact_cost, handling_cost: me.data.handling_cost, suppliers_default: me.data.suppliers_default, limit_default: me.data.limit_default }); }, [me.data]);
  const save = useMutation({
    mutationFn: () => cabApi("/settings", { method: "PATCH", body: { contact_cost: Number(f.contact_cost) || 0, handling_cost: Number(f.handling_cost) || 0, suppliers_default: f.suppliers_default, limit_default: Number(f.limit_default) || 0 } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["cab-me"] }); notifications.show({ color: "green", message: "Сохранено" }); },
    onError: (e: any) => notifications.show({ color: "red", message: e.message }),
  });
  const m = me.data;
  const hookUrl = m ? `${location.origin}/api/cab/hook/${m.hook_token}` : "";
  const supOpts = (suppliers.data?.items || []).filter((s: any) => s.available);
  const tg = useQuery({ queryKey: ["cab-telegram"], queryFn: () => cabApi("/integrations/telegram"), refetchInterval: 15_000 });
  const tgOff = useMutation({ mutationFn: () => cabApi("/integrations/telegram/disconnect", { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["cab-telegram"] }), onError: (e: any) => notifications.show({ color: "red", message: e.message }) });
  const tgTest = useMutation({ mutationFn: () => cabApi("/integrations/telegram/test", { method: "POST" }), onSuccess: () => notifications.show({ color: "green", message: "Сводка отправлена в Telegram" }), onError: (e: any) => notifications.show({ color: "red", message: e.message }) });
  const t = tg.data;
  return (
    <>
      <Group justify="space-between" mb="md"><Title order={2}>Настройки</Title><Button loading={save.isPending} onClick={() => save.mutate()}>Сохранить</Button></Group>
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <Stack>
          <Paper>
            <Text fw={600} mb="xs">Экономика для вкладки «Компании»</Text>
            <Group grow>
              <NumberInput label="Цена покупки контакта, ₽" value={f.contact_cost ?? 0} onChange={(v) => setF({ ...f, contact_cost: v })} min={0} decimalScale={2} />
              <NumberInput label="Цена обработки контакта, ₽" value={f.handling_cost ?? 0} onChange={(v) => setF({ ...f, handling_cost: v })} min={0} decimalScale={2} />
            </Group>
            <Text size="xs" c="dimmed" mt="xs">Стоимость лида = (куплено × покупка + куплено × обработка) / лидов. Цена заявки у Leads Factory: {m?.answer_cost ?? "—"} ₽.</Text>
          </Paper>
          <Paper>
            <Text fw={600} mb="xs">Умолчания при добавлении источников</Text>
            <Checkbox.Group label="Поставщики" value={f.suppliers_default || []} onChange={(v) => setF({ ...f, suppliers_default: v })}>
              <Group gap="sm" mt={4}>{supOpts.map((s: any) => <Checkbox key={s.code} value={s.code} label={s.label} />)}</Group>
            </Checkbox.Group>
            <NumberInput label="Лимит на связку в сутки" value={f.limit_default ?? 5} onChange={(v) => setF({ ...f, limit_default: v })} min={0} mt="xs" w={220} />
          </Paper>
        </Stack>
        <Stack>
          <Paper>
            <Text fw={600} mb="xs">Статусы по вебхуку</Text>
            <Text size="sm">Ваши сервисы шлют статус по номеру на адрес:</Text>
            <Code block mt={4} style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{hookUrl}</Code>
            <Text size="sm" mt="xs">POST с JSON <Code>{`{"phone": "79991112233", "status": "lead"}`}</Code> или GET <Code>?phone=…&status=…</Code>. Статусы: <Code>unsuccessful</Code> неуспешный, <Code>lead</Code> лид, <Code>qual</Code> квал-лид (принимаем и по-русски). Номер ищется среди купленных контактов, результат ложится в «Компании» и «Базу».</Text>
          </Paper>
          <Paper>
            <Group justify="space-between" mb="xs"><Text fw={600}>Telegram — сводка в 19:00 МСК</Text>{t && <Badge size="sm" variant="light" color={t.connected ? "green" : "gray"}>{t.connected ? "подключён" : "не подключён"}</Badge>}</Group>
            {!t ? <Text size="sm" c="dimmed">…</Text> : !t.configured || !t.bot ? <Text size="sm" c="dimmed">Бот у нас пока не настроен — напишите администратору.</Text> : t.connected ? (
              <Group gap="xs">
                <Text size="sm">Каждый день в 19:00 МСК бот <Anchor href={`https://t.me/${t.bot}`} target="_blank">@{t.bot}</Anchor> присылает: куплено сегодня, за неделю, баланс и на сколько дней его хватит, топ источников. Команда /stats — сводка сейчас.</Text>
                <Button size="xs" variant="light" loading={tgTest.isPending} onClick={() => tgTest.mutate()}>Прислать сводку сейчас</Button>
                <Button size="xs" variant="subtle" color="red" loading={tgOff.isPending} onClick={() => tgOff.mutate()}>Отключить</Button>
              </Group>
            ) : (
              <Stack gap="xs">
                <Text size="sm">Нажмите кнопку — откроется Telegram, там нажмите «Start». Уведомления получает только тот, кто подключил.</Text>
                <Button component="a" href={t.link} target="_blank" w="fit-content">Подключить бота @{t.bot}</Button>
                <Text size="xs" c="dimmed">или отправьте боту команду <Code>/start {t.code}</Code></Text>
              </Stack>
            )}
          </Paper>
          <Paper>
            <Text fw={600} mb="xs">Куда уходят контакты</Text>
            <Text size="sm" c="dimmed">Google Таблицы, Bitrix24, AmoCRM и свой URL — во вкладке «Интеграции».</Text>
          </Paper>
        </Stack>
      </SimpleGrid>
    </>
  );
}
