import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Checkbox, Collapse, Group, MultiSelect, NumberInput, Pagination, Paper, Select, SimpleGrid, Table, Text, TextInput, Textarea, Title, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconChevronDown, IconChevronUp, IconPlus } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { SUPPLIER_LABEL, WEEKDAYS, cabApi, qs } from "../api";
import { useMe } from "../CabinetApp";
import { dt, n } from "../../ui";

const STATUS = [{ value: "", label: "все" }, { value: "on", label: "включённые" }, { value: "off", label: "выключенные" }, { value: "pending", label: "ждут LF" }, { value: "error", label: "с ошибкой" }];

export default function Sources() {
  const qc = useQueryClient();
  const me = useMe();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [delim, setDelim] = useState(";");
  const [sup, setSup] = useState<string[]>([]);
  const [limit, setLimit] = useState<number | string>(5);
  const [geoAdd, setGeoAdd] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState("added_at");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState("20");
  const [sel, setSel] = useState<number[]>([]);
  const [bulkLimit, setBulkLimit] = useState<number | string>(5);
  const [bulkSup, setBulkSup] = useState<string[]>([]);
  const [bulkGeo, setBulkGeo] = useState<string[]>([]);
  const [days, setDays] = useState<boolean[]>([true, true, true, true, true, true, true]);

  useEffect(() => { if (me.data) { setSup(me.data.suppliers_default); setLimit(me.data.limit_default); setDays(me.data.weekdays); } }, [me.data]);
  useEffect(() => { setPage(1); setSel([]); }, [search, status, sort, order, size]);

  const suppliers = useQuery({ queryKey: ["cab-suppliers"], queryFn: () => cabApi("/suppliers") });
  const geo = useQuery({ queryKey: ["cab-geo"], queryFn: () => cabApi("/geo"), staleTime: 3_600_000 });
  const lim = size === "all" ? 5000 : Number(size);
  const params = { search, status, sort, order, page, limit: lim };
  const list = useQuery({ queryKey: ["cab-sources", params], queryFn: () => cabApi(`/sources${qs(params)}`), refetchInterval: 30_000 });
  const err = (e: any) => notifications.show({ color: "red", message: e.message });
  const bust = () => { qc.invalidateQueries({ queryKey: ["cab-sources"] }); qc.invalidateQueries({ queryKey: ["cab-me"] }); };

  const add = useMutation({
    mutationFn: () => cabApi("/sources", { method: "POST", body: { text, delimiter: delim, suppliers: sup, limit: Number(limit) || 0, geo_ids: geoAdd.map(Number) } }),
    onSuccess: (r: any) => { setText(""); bust(); notifications.show({ color: "green", message: `Добавлено ${r.added}, дублей ${r.duplicates}, нераспознано ${r.invalid_count}. ${r.note}` }); },
    onError: err,
  });
  const bulk = useMutation({
    mutationFn: (b: any) => cabApi("/sources/bulk", { method: "POST", body: { ids: sel, ...b } }),
    onSuccess: (r: any) => { bust(); notifications.show({ color: "green", message: `Изменено ${r.updated} источников — уйдёт в LF в течение минуты` }); },
    onError: err,
  });
  const saveDays = useMutation({
    mutationFn: (d: boolean[]) => cabApi("/settings", { method: "PATCH", body: { weekdays: d } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["cab-me"] }); notifications.show({ color: "green", message: "Расписание сохранено. Применится вечером до 20:00 МСК на следующий день" }); },
    onError: err,
  });

  const items: any[] = list.data?.items || [];
  const total: number = list.data?.total || 0;
  const pages = Math.max(1, Math.ceil(total / lim));
  const supOpts = (suppliers.data?.items || []).filter((s: any) => s.available).map((s: any) => ({ value: s.code, label: s.label }));
  const geoOpts = useMemo(() => (geo.data?.items || []).map((g: any) => ({ value: String(g.id), label: g.name })), [geo.data]);
  const geoName = (id: number) => geoOpts.find((g: any) => g.value === String(id))?.label || String(id);
  const th = (key: string, label: string) => (
    <Table.Th style={{ cursor: "pointer", whiteSpace: "nowrap" }} onClick={() => { if (sort === key) setOrder(order === "asc" ? "desc" : "asc"); else { setSort(key); setOrder("desc"); } }}>
      {label} {sort === key ? (order === "asc" ? <IconChevronUp size={12} /> : <IconChevronDown size={12} />) : null}
    </Table.Th>
  );

  return (
    <>
      <Group justify="space-between" mb="sm">
        <div><Title order={2}>Источники</Title><Text c="dimmed" size="sm">{n(total)} номеров · закупка по выбранным поставщикам, лимит на связку номер × поставщик в сутки</Text></div>
        <Button size="xs" leftSection={<IconPlus size={14} />} onClick={() => setOpen(!open)}>Добавить источники</Button>
      </Group>

      <Collapse in={open}>
        <Paper mb="sm">
          <SimpleGrid cols={{ base: 1, md: 2 }}>
            <Textarea label="Номера" description={`по одному в строке; можно «номер${delim}компания»`} autosize minRows={5} maxRows={14} value={text} onChange={(e) => setText(e.currentTarget.value)} placeholder={`79112223344${delim}Алые паруса\n78123334455`} />
            <div>
              <Select label="Разделитель" value={delim} onChange={(v) => setDelim(v || ";")} data={[{ value: ";", label: "; точка с запятой" }, { value: ",", label: ", запятая" }, { value: "\t", label: "табуляция" }, { value: "|", label: "| вертикальная черта" }]} />
              <Checkbox.Group label="Поставщики" value={sup} onChange={setSup} mt="xs">
                <Group gap="sm" mt={4}>{supOpts.map((s: any) => <Checkbox key={s.value} value={s.value} label={s.label} />)}</Group>
              </Checkbox.Group>
              <NumberInput label="Лимит на связку в сутки" value={limit} onChange={setLimit} min={0} mt="xs" />
              <MultiSelect label="Регионы (необязательно)" data={geoOpts} value={geoAdd} onChange={setGeoAdd} searchable clearable mt="xs" />
              <Button mt="sm" loading={add.isPending} disabled={!text.trim() || !sup.length} onClick={() => add.mutate()}>Добавить</Button>
            </div>
          </SimpleGrid>
        </Paper>
      </Collapse>

      <Paper mb="sm" p="xs">
        <Group gap="md" align="center">
          <Text size="sm" fw={500}>Дни закупки:</Text>
          {WEEKDAYS.map((d, i) => <Checkbox key={d} size="xs" label={d} checked={!!days[i]} onChange={(e) => { const nd = [...days]; nd[i] = e.currentTarget.checked; setDays(nd); }} />)}
          <Button size="compact-xs" variant="light" loading={saveDays.isPending} onClick={() => saveDays.mutate(days)}>Сохранить</Button>
          <Text size="xs" c="dimmed">не отмечен — накануне в 19:40 МСК источники выключаются, отмечен — включаются обратно; выключенные вами вручную не трогаем</Text>
        </Group>
      </Paper>

      <Group mb="xs" gap={6}>
        <TextInput size="xs" w={220} maw="100%" placeholder="номер или компания…" value={search} onChange={(e) => setSearch(e.currentTarget.value)} />
        <Select size="xs" w={150} value={status} onChange={(v) => setStatus(v || "")} data={STATUS} />
        <Select size="xs" w={90} value={size} onChange={(v) => setSize(v || "20")} data={[{ value: "20", label: "20" }, { value: "50", label: "50" }, { value: "100", label: "100" }, { value: "all", label: "все" }]} />
      </Group>

      {sel.length > 0 && (
        <Paper mb="xs" p="xs" withBorder style={{ borderColor: "var(--mantine-color-teal-4)" }}>
          <Group gap="sm" align="flex-end">
            <Text size="sm" fw={600}>Выбрано {sel.length}</Text>
            <Group gap={4} align="flex-end"><NumberInput size="xs" w={90} label="лимит" value={bulkLimit} onChange={setBulkLimit} min={0} /><Button size="xs" variant="light" onClick={() => bulk.mutate({ action: "limit", value: Number(bulkLimit) })}>применить</Button></Group>
            <Button size="xs" variant="light" color="green" onClick={() => bulk.mutate({ action: "enable" })}>включить</Button>
            <Button size="xs" variant="light" color="red" onClick={() => bulk.mutate({ action: "disable" })}>выключить</Button>
            <Group gap={4} align="flex-end"><MultiSelect size="xs" w={260} label="поставщики" data={supOpts} value={bulkSup} onChange={setBulkSup} /><Button size="xs" variant="light" disabled={!bulkSup.length} onClick={() => bulk.mutate({ action: "suppliers", value: bulkSup })}>задать</Button></Group>
            <Group gap={4} align="flex-end"><MultiSelect size="xs" w={260} label="регионы" data={geoOpts} value={bulkGeo} onChange={setBulkGeo} searchable /><Button size="xs" variant="light" disabled={!bulkGeo.length} onClick={() => bulk.mutate({ action: "geo_add", value: bulkGeo.map(Number) })}>добавить</Button><Button size="xs" variant="subtle" disabled={!bulkGeo.length} onClick={() => bulk.mutate({ action: "geo_remove", value: bulkGeo.map(Number) })}>убрать</Button></Group>
            <Button size="xs" variant="subtle" color="gray" onClick={() => setSel([])}>снять выбор</Button>
          </Group>
        </Paper>
      )}

      <Paper p="xs" style={{ overflowX: "auto" }}>
        <Table fz="xs" verticalSpacing={5} horizontalSpacing="xs" className="compact">
          <Table.Thead><Table.Tr>
            <Table.Th w={28}><Checkbox size="xs" checked={!!items.length && sel.length === items.length} indeterminate={!!sel.length && sel.length < items.length} onChange={(e) => setSel(e.currentTarget.checked ? items.map((s) => s.id) : [])} /></Table.Th>
            {th("id", "ID LF")}{th("phone", "Номер")}{th("company", "Компания")}{th("added_at", "Добавлен")}{th("enabled", "Статус")}
            <Table.Th>Поставщики</Table.Th>{th("limit", "Лимит")}{th("contacts_total", "Всего")}{th("contacts_today", "Сегодня")}{th("repeats_total", "Повторов")}{th("last_contact_at", "Последний")}<Table.Th>Регионы</Table.Th>
          </Table.Tr></Table.Thead>
          <Table.Tbody>
            {items.map((s) => (
              <Table.Tr key={s.id}>
                <Table.Td><Checkbox size="xs" checked={sel.includes(s.id)} onChange={(e) => setSel(e.currentTarget.checked ? [...sel, s.id] : sel.filter((x) => x !== s.id))} /></Table.Td>
                <Table.Td className="num">{s.lf_source_id ?? <Text span c="dimmed">…</Text>}</Table.Td>
                <Table.Td className="mono">{s.phone}</Table.Td>
                <Table.Td>{s.company || <Text span c="dimmed">—</Text>}</Table.Td>
                <Table.Td className="num">{dt(s.added_at)}</Table.Td>
                <Table.Td style={{ whiteSpace: "nowrap" }}>
                  {s.enabled ? <Badge size="xs" color="green" variant="light">включён</Badge> : <Badge size="xs" color="gray" variant="light">{s.enabled_by_user ? "выкл расписанием" : "выключен"}</Badge>}
                  {s.lf_dirty && <Tooltip label="изменения ещё не ушли в Leads Factory"><Badge size="xs" color="yellow" variant="light" ml={4}>→ LF</Badge></Tooltip>}
                  {s.lf_error && <Tooltip label={s.lf_error}><Badge size="xs" color="red" variant="light" ml={4}>ошибка</Badge></Tooltip>}
                </Table.Td>
                <Table.Td><Group gap={2}>{(s.suppliers || []).map((x: string) => <Badge key={x} size="xs" variant="outline" color="teal" title={SUPPLIER_LABEL[x]}>{x}</Badge>)}</Group></Table.Td>
                <Table.Td className="num">{s.limit}</Table.Td>
                <Table.Td className="num">{n(s.contacts_total)}</Table.Td>
                <Table.Td className="num">{n(s.contacts_today)}</Table.Td>
                <Table.Td className="num">{n(s.repeats_total)}</Table.Td>
                <Table.Td className="num">{dt(s.last_contact_at)}</Table.Td>
                <Table.Td><Text size="xs" c="dimmed" className="clip" style={{ maxWidth: 200 }}>{(s.geo_ids || []).map(geoName).join(", ") || "—"}</Text></Table.Td>
              </Table.Tr>
            ))}
            {!items.length && <Table.Tr><Table.Td colSpan={13}><Text c="dimmed" ta="center">{list.isLoading ? "загрузка…" : "источников нет — добавьте первые"}</Text></Table.Td></Table.Tr>}
          </Table.Tbody>
        </Table>
        <Group justify="space-between" mt="xs">
          <Text size="xs" c="dimmed">{total ? `${(page - 1) * lim + 1}–${Math.min(page * lim, total)} из ${n(total)}` : ""}</Text>
          <Pagination size="sm" value={page} onChange={setPage} total={pages} />
        </Group>
      </Paper>
    </>
  );
}
