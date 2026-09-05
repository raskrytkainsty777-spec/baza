import { Paper, Table, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { cabApi } from "../api";
import { money, n } from "../../ui";

export default function Companies() {
  const q = useQuery({ queryKey: ["cab-companies"], queryFn: () => cabApi("/companies"), refetchInterval: 60_000 });
  const items: any[] = q.data?.items || [];
  const pct = (v: number | null) => (v == null ? "—" : `${v}%`);
  return (
    <>
      <Title order={2} mb={4}>Компании</Title>
      <Text c="dimmed" size="sm" mb="md">откуда взяты номера-источники · статусы приходят по вебхуку · стоимость = (куплено × цена покупки + куплено × цена обработки) / лидов; цены — в Настройках ({money(q.data?.contact_cost)} + {money(q.data?.handling_cost)})</Text>
      <Paper p="xs" style={{ overflowX: "auto" }}>
        <Table fz="xs" verticalSpacing={5} className="compact">
          <Table.Thead><Table.Tr>
            <Table.Th>Компания</Table.Th><Table.Th ta="right">Источников</Table.Th><Table.Th ta="right">Куплено</Table.Th><Table.Th ta="right">Лидов</Table.Th><Table.Th ta="right">Неуспешных</Table.Th><Table.Th ta="right">Квал-лидов</Table.Th><Table.Th ta="right">Конв. лид</Table.Th><Table.Th ta="right">Конв. квал</Table.Th><Table.Th ta="right">Потрачено</Table.Th><Table.Th ta="right">₽ / лид</Table.Th><Table.Th ta="right">₽ / квал</Table.Th>
          </Table.Tr></Table.Thead>
          <Table.Tbody>
            {items.map((c) => (
              <Table.Tr key={c.id}>
                <Table.Td>{c.name}</Table.Td>
                <Table.Td className="num" ta="right">{n(c.sources)}</Table.Td><Table.Td className="num" ta="right">{n(c.contacts)}</Table.Td>
                <Table.Td className="num" ta="right">{n(c.leads)}</Table.Td><Table.Td className="num" ta="right">{n(c.unsuccessful)}</Table.Td><Table.Td className="num" ta="right">{n(c.quals)}</Table.Td>
                <Table.Td className="num" ta="right">{pct(c.conversion_lead)}</Table.Td><Table.Td className="num" ta="right">{pct(c.conversion_qual)}</Table.Td>
                <Table.Td className="num" ta="right">{money(c.spend)}</Table.Td><Table.Td className="num" ta="right">{money(c.cost_per_lead)}</Table.Td><Table.Td className="num" ta="right">{money(c.cost_per_qual)}</Table.Td>
              </Table.Tr>
            ))}
            {!items.length && <Table.Tr><Table.Td colSpan={11}><Text c="dimmed" ta="center">компаний нет — они появляются из строк «номер;компания» при добавлении источников</Text></Table.Td></Table.Tr>}
          </Table.Tbody>
        </Table>
      </Paper>
    </>
  );
}
