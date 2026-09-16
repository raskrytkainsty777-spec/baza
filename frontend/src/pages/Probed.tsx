import { useState } from "react";
import { Badge, Button, Group, Pagination, Paper, Select, Table, Text, TextInput, Title, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconDownload } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { api, getToken, qs } from "../api";
import { Kpi, KpiRow, cityOptions, dt, n, useCities } from "../ui";

const SOURCE = [
  { value: "", label: "все источники номера" },
  { value: "probe", label: "нашёл пробив" },
  { value: "base", label: "был в базе" },
];
const CRM = [
  { value: "", label: "любой статус CRM" },
  { value: "none", label: "без статуса" },
  { value: "application", label: "заявка" },
  { value: "qual", label: "квал" },
  { value: "deal", label: "сделка" },
  { value: "negative", label: "негатив" },
];
const SIZES = ["50", "100", "200"];

const isoDay = (shift = 0) => {
  const d = new Date();
  d.setDate(d.getDate() + shift);
  return d.toISOString().slice(0, 10);
};

export default function Probed() {
  const cities = useCities();
  const [city, setCity] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [q, setQ] = useState("");
  const [source, setSource] = useState("");
  const [crm, setCrm] = useState("");
  const [size, setSize] = useState("100");
  const [page, setPage] = useState(1);

  const limit = Number(size) || 100;
  const params = { city_id: city, date_from: from, date_to: to, q, source, crm_status: crm,
                   limit, offset: (page - 1) * limit };
  const list = useQuery({ queryKey: ["probed", params], queryFn: () => api(`/leads/probed${qs(params)}`) });
  const byDay = useQuery({ queryKey: ["probed-days", city], queryFn: () => api(`/leads/probed/by-day${qs({ city_id: city, days: 14 })}`) });

  const items: any[] = list.data?.items || [];
  const total: number = list.data?.total || 0;
  const pages = Math.max(1, Math.ceil(total / limit));
  const setRange = (a: string, b: string) => { setFrom(a); setTo(b); setPage(1); };

  const download = async () => {
    const res = await fetch(`/api/leads/probed.csv${qs({ city_id: city, date_from: from, date_to: to, q, source, crm_status: crm })}`,
      { headers: { Authorization: `Bearer ${getToken()}` } });
    if (!res.ok) { notifications.show({ color: "red", message: `Не удалось скачать: ${res.status}` }); return; }
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `probed${from ? "_" + from : ""}${to ? "_" + to : ""}.csv`;
    a.click();
  };

  return (
    <>
      <Group justify="space-between" mb="sm">
        <div>
          <Title order={2}>Пробитая база</Title>
          <Text c="dimmed" size="sm">лиды с номером · дата пробива в московском времени · выгрузка учитывает фильтры</Text>
        </div>
        <Button leftSection={<IconDownload size={16} />} onClick={download} disabled={!total}>
          Скачать CSV · {n(total)}
        </Button>
      </Group>

      <KpiRow>
        <Kpi value={n(total)} label="в выборке" hint={`уникальных номеров ${n(list.data?.unique_phones)}`} />
        <Kpi value={n(list.data?.today)} label="пробито сегодня" hint="по выбранному городу" />
        <Kpi value={n(byDay.data?.items?.[0]?.leads)} label={`за ${byDay.data?.items?.[0]?.day || "последний день"}`}
             hint={`номеров ${n(byDay.data?.items?.[0]?.phones)}`} />
      </KpiRow>

      <Paper mb="xs">
        <Group gap={6} align="flex-end">
          <Select size="xs" w={190} label="город" value={city} onChange={(v) => { setCity(v || ""); setPage(1); }}
                  data={cityOptions(cities.data?.cities)} />
          <TextInput size="xs" type="date" label="пробит с" value={from} onChange={(e) => { setFrom(e.currentTarget.value); setPage(1); }} />
          <TextInput size="xs" type="date" label="по" value={to} onChange={(e) => { setTo(e.currentTarget.value); setPage(1); }} />
          <Select size="xs" w={180} label="номер" value={source} onChange={(v) => { setSource(v || ""); setPage(1); }} data={SOURCE} />
          <Select size="xs" w={170} label="CRM" value={crm} onChange={(v) => { setCrm(v || ""); setPage(1); }} data={CRM} />
          <TextInput size="xs" w={190} label="поиск" placeholder="номер, логин, текст" value={q}
                     onChange={(e) => { setQ(e.currentTarget.value); setPage(1); }} />
          <Select size="xs" w={90} label="на странице" value={size} onChange={(v) => { setSize(v || "100"); setPage(1); }}
                  data={SIZES.map((x) => ({ value: x, label: x }))} />
        </Group>
        <Group gap={6} mt="xs">
          <Text size="xs" c="dimmed">быстро:</Text>
          <Button size="compact-xs" variant="light" onClick={() => setRange(isoDay(), isoDay())}>сегодня</Button>
          <Button size="compact-xs" variant="light" onClick={() => setRange(isoDay(-1), isoDay(-1))}>вчера</Button>
          <Button size="compact-xs" variant="light" onClick={() => setRange(isoDay(-6), isoDay())}>7 дней</Button>
          <Button size="compact-xs" variant="light" onClick={() => setRange(isoDay(-29), isoDay())}>30 дней</Button>
          <Button size="compact-xs" variant="subtle" onClick={() => setRange("", "")}>весь период</Button>
        </Group>
      </Paper>

      <Paper p="xs" style={{ overflowX: "auto" }}>
        <Table fz="xs" verticalSpacing={5} horizontalSpacing="xs" className="compact">
          <Table.Thead><Table.Tr>
            <Table.Th>Телефон</Table.Th><Table.Th>Пробит</Table.Th><Table.Th>Откуда</Table.Th>
            <Table.Th>Логин</Table.Th><Table.Th>Город</Table.Th><Table.Th>Донор</Table.Th>
            <Table.Th>На что привлёкся</Table.Th><Table.Th>Комментарий</Table.Th>
            <Table.Th>Пост</Table.Th><Table.Th>CRM</Table.Th>
          </Table.Tr></Table.Thead>
          <Table.Tbody>
            {items.map((x) => (
              <Table.Tr key={x.id}>
                <Table.Td className="mono" style={{ whiteSpace: "nowrap" }}>{x.phone}</Table.Td>
                <Table.Td style={{ whiteSpace: "nowrap" }}>{dt(x.probed_at)}</Table.Td>
                <Table.Td>{x.phone_from === "base"
                  ? <Badge size="xs" variant="light" color="gray">из базы</Badge>
                  : <Badge size="xs" variant="light" color="green">пробив</Badge>}</Table.Td>
                <Table.Td><a className="rowlink mono" href={`https://instagram.com/${x.username}`} target="_blank" rel="noreferrer">{x.username}</a></Table.Td>
                <Table.Td style={{ whiteSpace: "nowrap" }}>{x.city || <Text span c="dimmed">—</Text>}</Table.Td>
                <Table.Td className="mono">{x.donor || <Text span c="dimmed">—</Text>}</Table.Td>
                <Table.Td><Text size="xs" className="clip2" style={{ maxWidth: 260 }} title={x.offer_text || x.offer || ""}>{x.offer_text || x.offer || "—"}</Text></Table.Td>
                <Table.Td><Text size="xs" className="clip2" style={{ maxWidth: 220 }} title={x.comment}>{x.comment}</Text></Table.Td>
                <Table.Td>{x.post_url
                  ? <Tooltip label={dt(x.post_published_at)}><a className="rowlink" href={x.post_url} target="_blank" rel="noreferrer">пост</a></Tooltip>
                  : <Text span c="dimmed">—</Text>}</Table.Td>
                <Table.Td>{x.crm_status || <Text span c="dimmed">—</Text>}</Table.Td>
              </Table.Tr>
            ))}
            {!items.length && <Table.Tr><Table.Td colSpan={10}><Text c="dimmed" ta="center">за выбранный период пробитых нет</Text></Table.Td></Table.Tr>}
          </Table.Tbody>
        </Table>
        {pages > 1 && <Group justify="center" mt="sm"><Pagination size="sm" total={pages} value={page} onChange={setPage} /></Group>}
      </Paper>
    </>
  );
}
