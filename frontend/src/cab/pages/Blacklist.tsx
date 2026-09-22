import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Badge, Button, Group, Paper, Table, Tabs, Text, TextInput, Textarea, Title, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cabApi } from "../api";
import { dt, n } from "../../ui";

const err = (e: any) => notifications.show({ color: "red", message: e.message });

/** Номера, которые не закупаем: уходят в проект LF как blacklist. */
function Contacts() {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const q = useQuery({ queryKey: ["cab-blacklist"], queryFn: () => cabApi("/blacklist"), refetchInterval: 60_000 });
  const add = useMutation({
    mutationFn: () => cabApi("/blacklist", { method: "POST", body: { text } }),
    onSuccess: (r: any) => { setText(""); qc.invalidateQueries({ queryKey: ["cab-blacklist"] }); notifications.show({ color: "green", message: `Добавлено ${r.added}, нераспознано ${r.invalid}. ${r.note}` }); },
    onError: err,
  });
  const items: any[] = q.data?.items || [];
  return (
    <>
      <Text c="dimmed" size="sm" mb="md">номера, которые не закупаем: ваши менеджеры, ваши клиенты. Уходят в проект LF · {n(items.length)} шт.</Text>
      <Paper mb="sm">
        <Group align="flex-end">
          <Textarea style={{ flex: 1 }} autosize minRows={2} maxRows={8} placeholder="79991112233, по одному в строке или через запятую" value={text} onChange={(e) => setText(e.currentTarget.value)} />
          <Button loading={add.isPending} disabled={!text.trim()} onClick={() => add.mutate()}>Добавить</Button>
        </Group>
      </Paper>
      <Paper p="xs">
        <Table fz="xs" verticalSpacing={4} className="compact">
          <Table.Thead><Table.Tr><Table.Th>Номер</Table.Th><Table.Th>Добавлен</Table.Th><Table.Th>В LF</Table.Th></Table.Tr></Table.Thead>
          <Table.Tbody>
            {items.map((b) => <Table.Tr key={b.id}><Table.Td className="mono">{b.phone}</Table.Td><Table.Td className="num">{dt(b.created_at)}</Table.Td><Table.Td>{b.sent_at ? <Badge size="xs" color="green" variant="light">ушёл {dt(b.sent_at)}</Badge> : <Badge size="xs" color="yellow" variant="light">в очереди</Badge>}</Table.Td></Table.Tr>)}
            {!items.length && <Table.Tr><Table.Td colSpan={3}><Text c="dimmed" ta="center">пусто</Text></Table.Td></Table.Tr>}
          </Table.Tbody>
        </Table>
      </Paper>
    </>
  );
}

/** Источники, которые нельзя включать никогда: попал в список — выключен сразу, появился снова — не включится. */
function SourcesBlacklist() {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const q = useQuery({ queryKey: ["cab-source-blacklist"], queryFn: () => cabApi("/source-blacklist"), refetchInterval: 60_000 });
  const bust = () => { qc.invalidateQueries({ queryKey: ["cab-source-blacklist"] }); qc.invalidateQueries({ queryKey: ["cab-sources"] }); };
  const add = useMutation({
    mutationFn: () => cabApi("/source-blacklist", { method: "POST", body: { text, note } }),
    onSuccess: (r: any) => {
      setText(""); setNote(""); bust();
      notifications.show({ color: "green", message: `В список: ${r.added}, уже были: ${r.already}, нераспознано: ${r.invalid}. Выключено источников сейчас: ${r.disabled}. ${r.note}` });
    },
    onError: err,
  });
  const del = useMutation({
    mutationFn: (id: number) => cabApi(`/source-blacklist/${id}`, { method: "DELETE" }),
    onSuccess: (r: any) => { bust(); notifications.show({ color: "green", message: `${r.phone} убран из списка. Источник остался выключенным — включить его можно вручную` }); },
    onError: err,
  });
  const askDel = (b: any) => {
    if (window.confirm(`Убрать ${b.phone} из чёрного списка? Источник останется выключенным, но его снова можно будет включить.`)) del.mutate(b.id);
  };
  const items: any[] = q.data?.items || [];
  const state = (b: any) => {
    if (!b.in_project) return <Badge size="xs" color="gray" variant="outline">нет в проекте</Badge>;
    if (b.enabled) return <Tooltip label="выключится в течение минуты"><Badge size="xs" color="red" variant="light">включён — выключается</Badge></Tooltip>;
    return (
      <>
        <Badge size="xs" color="gray" variant="light">выключен</Badge>
        {b.lf_will_work && <Tooltip label="в LF ещё включён, выключение уходит в течение минуты"><Badge size="xs" color="yellow" variant="light" ml={4}>→ LF</Badge></Tooltip>}
      </>
    );
  };
  return (
    <>
      <Text c="dimmed" size="sm" mb="md">
        источники, которые нельзя включать никогда · {n(items.length)} шт. Попал в список — выключается сразу и в LF.
        Появится снова руками, от агента досбора или из LF — останется выключенным, «включить» его не сможет никто.
      </Text>
      <Paper mb="sm">
        <Group align="flex-end">
          <Textarea style={{ flex: 1 }} autosize minRows={2} maxRows={8} placeholder="79991112233, по одному в строке или через запятую" value={text} onChange={(e) => setText(e.currentTarget.value)} />
          <TextInput w={260} maw="100%" label="Примечание" placeholder="почему нельзя (необязательно)" value={note} onChange={(e) => setNote(e.currentTarget.value)} />
          <Button color="dark" loading={add.isPending} disabled={!text.trim()} onClick={() => add.mutate()}>В чёрный список</Button>
        </Group>
        <Text size="xs" c="dimmed" mt={6}>Добавить сразу несколько из таблицы: отметьте источники на странице «Источники» и нажмите «в чёрный список».</Text>
      </Paper>
      <Paper p="xs" style={{ overflowX: "auto" }}>
        <Table fz="xs" verticalSpacing={4} className="compact">
          <Table.Thead><Table.Tr><Table.Th>Номер</Table.Th><Table.Th>Компания</Table.Th><Table.Th>Источник</Table.Th><Table.Th>Примечание</Table.Th><Table.Th>Добавлен</Table.Th><Table.Th /></Table.Tr></Table.Thead>
          <Table.Tbody>
            {items.map((b) => (
              <Table.Tr key={b.id}>
                <Table.Td className="mono">{b.phone}</Table.Td>
                <Table.Td>{b.company || <Text span c="dimmed">—</Text>}</Table.Td>
                <Table.Td style={{ whiteSpace: "nowrap" }}>{state(b)}</Table.Td>
                <Table.Td><Text size="xs" c="dimmed" className="clip" style={{ maxWidth: 260 }}>{b.note || "—"}</Text></Table.Td>
                <Table.Td className="num">{dt(b.created_at)}</Table.Td>
                <Table.Td><Button size="compact-xs" variant="subtle" color="gray" loading={del.isPending && del.variables === b.id} onClick={() => askDel(b)}>убрать</Button></Table.Td>
              </Table.Tr>
            ))}
            {!items.length && <Table.Tr><Table.Td colSpan={6}><Text c="dimmed" ta="center">{q.isLoading ? "загрузка…" : "пусто"}</Text></Table.Td></Table.Tr>}
          </Table.Tbody>
        </Table>
      </Paper>
    </>
  );
}

export default function Blacklist() {
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") === "sources" ? "sources" : "contacts";
  return (
    <>
      <Title order={2} mb="sm">Чёрный список</Title>
      <Tabs value={tab} onChange={(v) => setSp(v === "sources" ? { tab: "sources" } : {})}>
        <Tabs.List mb="sm"><Tabs.Tab value="contacts">Контакты</Tabs.Tab><Tabs.Tab value="sources">Источники</Tabs.Tab></Tabs.List>
        <Tabs.Panel value="contacts"><Contacts /></Tabs.Panel>
        <Tabs.Panel value="sources"><SourcesBlacklist /></Tabs.Panel>
      </Tabs>
    </>
  );
}
