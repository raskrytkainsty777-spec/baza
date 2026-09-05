import { useState } from "react";
import { Badge, Button, Group, Paper, Table, Text, Textarea, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cabApi } from "../api";
import { dt, n } from "../../ui";

export default function Blacklist() {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const q = useQuery({ queryKey: ["cab-blacklist"], queryFn: () => cabApi("/blacklist"), refetchInterval: 60_000 });
  const add = useMutation({
    mutationFn: () => cabApi("/blacklist", { method: "POST", body: { text } }),
    onSuccess: (r: any) => { setText(""); qc.invalidateQueries({ queryKey: ["cab-blacklist"] }); notifications.show({ color: "green", message: `Добавлено ${r.added}, нераспознано ${r.invalid}. ${r.note}` }); },
    onError: (e: any) => notifications.show({ color: "red", message: e.message }),
  });
  const items: any[] = q.data?.items || [];
  return (
    <>
      <Title order={2} mb={4}>Чёрный список</Title>
      <Text c="dimmed" size="sm" mb="md">номера, которые не закупаем: ваши менеджеры, ваши клиенты. Уходят в проект Leads Factory · {n(items.length)} шт.</Text>
      <Paper mb="sm">
        <Group align="flex-end">
          <Textarea style={{ flex: 1 }} autosize minRows={2} maxRows={8} placeholder="79991112233, по одному в строке или через запятую" value={text} onChange={(e) => setText(e.currentTarget.value)} />
          <Button loading={add.isPending} disabled={!text.trim()} onClick={() => add.mutate()}>Добавить</Button>
        </Group>
      </Paper>
      <Paper p="xs">
        <Table fz="xs" verticalSpacing={4} className="compact">
          <Table.Thead><Table.Tr><Table.Th>Номер</Table.Th><Table.Th>Добавлен</Table.Th><Table.Th>В Leads Factory</Table.Th></Table.Tr></Table.Thead>
          <Table.Tbody>
            {items.map((b) => <Table.Tr key={b.id}><Table.Td className="mono">{b.phone}</Table.Td><Table.Td className="num">{dt(b.created_at)}</Table.Td><Table.Td>{b.sent_at ? <Badge size="xs" color="green" variant="light">ушёл {dt(b.sent_at)}</Badge> : <Badge size="xs" color="yellow" variant="light">в очереди</Badge>}</Table.Td></Table.Tr>)}
            {!items.length && <Table.Tr><Table.Td colSpan={3}><Text c="dimmed" ta="center">пусто</Text></Table.Td></Table.Tr>}
          </Table.Tbody>
        </Table>
      </Paper>
    </>
  );
}
