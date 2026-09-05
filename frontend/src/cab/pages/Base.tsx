import { useEffect, useState } from "react";
import { Badge, Button, Group, Pagination, Paper, Select, Table, Text, TextInput, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconDownload } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { cabApi, cabToken, qs } from "../api";
import { dt, n } from "../../ui";

const HOOK: Record<string, [string, string]> = { lead: ["green", "лид"], qual: ["teal", "квал-лид"], unsuccessful: ["red", "неуспешный"] };

export default function Base() {
  const [search, setSearch] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState("50");
  useEffect(() => setPage(1), [search, from, to, status, size]);
  const lim = Number(size);
  const params = { search, date_from: from, date_to: to, status, page, limit: lim };
  const q = useQuery({ queryKey: ["cab-contacts", params], queryFn: () => cabApi(`/contacts${qs(params)}`), refetchInterval: 60_000 });
  const stats = useQuery({ queryKey: ["cab-stats"], queryFn: () => cabApi("/stats"), refetchInterval: 120_000 });
  const items: any[] = q.data?.items || [];
  const total: number = q.data?.total || 0;

  const download = async () => {
    const res = await fetch(`/api/cab/contacts/export.csv${qs({ search, date_from: from, date_to: to, status })}`, { headers: { Authorization: `Bearer ${cabToken()}` } });
    if (!res.ok) { notifications.show({ color: "red", message: "Выгрузка не удалась" }); return; }
    const blob = await res.blob(); const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "contacts.csv"; a.click();
  };

  return (
    <>
      <Group justify="space-between" mb="sm">
        <div><Title order={2}>База</Title><Text c="dimmed" size="sm">копия заявок Leads Factory по проекту · {n(total)} строк · в среднем {stats.data?.avg_per_day ?? "—"} в день{stats.data?.days_left != null ? ` · баланса хватит на ~${stats.data.days_left} дн` : ""}</Text></div>
        <Button size="xs" variant="light" leftSection={<IconDownload size={14} />} onClick={download}>CSV</Button>
      </Group>
      <Group mb="xs" gap={6}>
        <TextInput size="xs" w={220} placeholder="номер, источник, компания…" value={search} onChange={(e) => setSearch(e.currentTarget.value)} />
        <TextInput size="xs" type="date" value={from} onChange={(e) => setFrom(e.currentTarget.value)} />
        <TextInput size="xs" type="date" value={to} onChange={(e) => setTo(e.currentTarget.value)} />
        <Select size="xs" w={150} value={status} onChange={(v) => setStatus(v || "")} data={[{ value: "", label: "любой статус" }, { value: "none", label: "без статуса" }, { value: "lead", label: "лид" }, { value: "qual", label: "квал-лид" }, { value: "unsuccessful", label: "неуспешный" }]} />
        <Select size="xs" w={80} value={size} onChange={(v) => setSize(v || "50")} data={["20", "50", "100", "500"]} />
      </Group>
      <Paper p="xs" style={{ overflowX: "auto" }}>
        <Table fz="xs" verticalSpacing={4} className="compact">
          <Table.Thead><Table.Tr><Table.Th>Оператор</Table.Th><Table.Th>Источник</Table.Th><Table.Th>Контакт</Table.Th><Table.Th>Дата выгрузки</Table.Th><Table.Th>Поставщик</Table.Th><Table.Th>Компания</Table.Th><Table.Th>Регион</Table.Th><Table.Th>LF</Table.Th><Table.Th>Статус</Table.Th></Table.Tr></Table.Thead>
          <Table.Tbody>
            {items.map((x) => (
              <Table.Tr key={x.id}>
                <Table.Td>{x.operator || "—"}</Table.Td><Table.Td className="mono">{x.source_phone || "—"}</Table.Td><Table.Td className="mono">{x.phone}</Table.Td>
                <Table.Td className="num">{dt(x.bought_at)}</Table.Td><Table.Td><Text size="xs">{x.supplier_label || "—"}</Text></Table.Td><Table.Td>{x.company || "—"}</Table.Td>
                <Table.Td><Text size="xs" c="dimmed">{x.region || "—"}</Text></Table.Td>
                <Table.Td>{x.lf_status === "repeat" ? <Badge size="xs" color="gray" variant="light">повтор</Badge> : <Badge size="xs" color="blue" variant="light">новая</Badge>}</Table.Td>
                <Table.Td>{x.hook_status ? <Badge size="xs" color={HOOK[x.hook_status]?.[0]} variant="light">{HOOK[x.hook_status]?.[1]}</Badge> : <Text size="xs" c="dimmed">—</Text>}</Table.Td>
              </Table.Tr>
            ))}
            {!items.length && <Table.Tr><Table.Td colSpan={9}><Text c="dimmed" ta="center">{q.isLoading ? "загрузка…" : "контактов пока нет"}</Text></Table.Td></Table.Tr>}
          </Table.Tbody>
        </Table>
        <Group justify="space-between" mt="xs">
          <Text size="xs" c="dimmed">{total ? `${(page - 1) * lim + 1}–${Math.min(page * lim, total)} из ${n(total)}` : ""}</Text>
          <Pagination size="sm" value={page} onChange={setPage} total={Math.max(1, Math.ceil(total / lim))} />
        </Group>
      </Paper>
    </>
  );
}
