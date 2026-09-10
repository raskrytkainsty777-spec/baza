import { useState } from "react";
import { Autocomplete, Button, Checkbox, Group, Modal, Paper, Select, Table, Text, Textarea, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconPlus } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cabApi } from "../api";
import { money, n } from "../../ui";

const RU: Record<string, string> = { lead: "лид", qual: "квал-лид", unsuccessful: "неуспешный" };
const STATE_LABEL: Record<string, string> = {
  ok: "можно проставить", upgrade: "повысим", has_status: "такой статус уже стоит",
  lower: "стоит более высокий — понижать нельзя", not_found: "не покупали этот контакт",
  bad: "не похоже на номер", dup: "повтор в списке",
};
const STATE_COLOR: Record<string, string> = { ok: "green.8", upgrade: "green.8", has_status: "orange.8", lower: "orange.8", not_found: "red.8", bad: "red.8", dup: "dimmed" };
const STATUS_OPTS = [
  { value: "lead", label: "лид" }, { value: "qual", label: "квал-лид" }, { value: "unsuccessful", label: "неуспешный" },
];

function AddStatus({ opened, onClose, onDone }: { opened: boolean; onClose: () => void; onDone: () => void }) {
  const [text, setText] = useState("");
  const [status, setStatus] = useState<string | null>("lead");
  const [res, setRes] = useState<any>(null);
  const err = (e: any) => notifications.show({ color: "red", message: e.message });
  const check = useMutation({
    mutationFn: () => cabApi("/statuses/check", { method: "POST", body: { text, status } }),
    onSuccess: setRes, onError: err,
  });
  const apply = useMutation({
    mutationFn: () => cabApi("/statuses/apply", { method: "POST", body: { phones: res.ready, status } }),
    onSuccess: (r: any) => {
      notifications.show({ color: "green", message: `Проставлено на ${r.updated} контактов` + (r.upgraded ? `, из них повышено с прежнего статуса ${r.upgraded}` : "") });
      setText(""); setRes(null); onDone(); onClose();
    },
    onError: err,
  });
  const close = () => { setText(""); setRes(null); onClose(); };
  const counts = res?.counts || {};
  return (
    <Modal opened={opened} onClose={close} size="lg" title="Добавить статус вручную">
      <Select label="Статус" description="неуспешный → лид → квал-лид: повысить можно, понизить нет" w={260} mb="sm"
        value={status} onChange={(v) => { setStatus(v); setRes(null); }} data={STATUS_OPTS} />
      <Textarea label="Номера — каждый с новой строки" description="+7, 8, скобки и пробелы убираются сами" autosize minRows={6} maxRows={14}
        placeholder={"+7 (999) 123-45-67\n89991234568\n79991234569"} value={text}
        onChange={(e) => { setText(e.currentTarget.value); setRes(null); }} />
      {!res && (
        <Button mt="sm" loading={check.isPending} disabled={!text.trim() || !status} onClick={() => check.mutate()}>Проверить номера</Button>
      )}
      {res && (
        <>
          <Group gap="md" mt="sm">
            <Text size="sm" fw={600} c="green.8">поставим «{RU[status || ""]}»: {n((counts.ok || 0) + (counts.upgrade || 0))}{counts.upgrade ? ` (из них повысим ${n(counts.upgrade)})` : ""}</Text>
            {!!counts.has_status && <Text size="sm" c="orange.8">такой статус уже стоит: {n(counts.has_status)}</Text>}
            {!!counts.lower && <Text size="sm" c="orange.8">стоит более высокий: {n(counts.lower)}</Text>}
            {!!counts.not_found && <Text size="sm" c="red.8">не покупали: {n(counts.not_found)}</Text>}
            {!!(counts.bad || counts.dup) && <Text size="sm" c="dimmed">повторы и мусор: {n((counts.bad || 0) + (counts.dup || 0))}</Text>}
          </Group>
          <Paper p="xs" mt="xs" withBorder style={{ maxHeight: 260, overflowY: "auto" }}>
            <Table fz="xs" verticalSpacing={3}>
              <Table.Thead><Table.Tr><Table.Th>Номер</Table.Th><Table.Th>Как ввели</Table.Th><Table.Th>Проверка</Table.Th></Table.Tr></Table.Thead>
              <Table.Tbody>
                {res.items.map((x: any, i: number) => (
                  <Table.Tr key={i}>
                    <Table.Td className="mono">{x.phone || "—"}</Table.Td>
                    <Table.Td><Text size="xs" c="dimmed">{x.input}</Text></Table.Td>
                    <Table.Td><Text size="xs" c={STATE_COLOR[x.state]}>{STATE_LABEL[x.state]}{x.status ? ` · сейчас ${RU[x.status] || x.status}` : ""}</Text></Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Paper>
          <Group mt="md" align="flex-end">
            <Button loading={apply.isPending} disabled={!res.ready?.length || !status} onClick={() => apply.mutate()}>
              Отправить · {n(res.ready?.length || 0)}
            </Button>
            <Button variant="subtle" onClick={() => setRes(null)}>Изменить список</Button>
          </Group>
          <Text size="xs" c="dimmed" mt="xs">У контакта один статус: при повышении прежний снимается, поэтому в статистике он переезжает из лидов в квал-лиды. Статистика обновится сразу.</Text>
        </>
      )}
    </Modal>
  );
}

export default function Companies() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState<number[]>([]);
  const [group, setGroup] = useState("");
  const q = useQuery({ queryKey: ["cab-companies"], queryFn: () => cabApi("/companies"), refetchInterval: 60_000 });
  const items: any[] = q.data?.items || [];
  const pct = (v: number | null) => (v == null ? "—" : `${v}%`);
  const bust = () => { qc.invalidateQueries({ queryKey: ["cab-companies"] }); qc.invalidateQueries({ queryKey: ["cab-sources"] }); };
  const bulk = useMutation({
    mutationFn: (b: any) => cabApi("/companies/bulk", { method: "POST", body: { ids: sel, ...b } }),
    onSuccess: (r: any, b: any) => {
      setSel([]); bust();
      notifications.show({ color: "green", message: b.action === "group"
        ? (r.group ? `Группа «${r.group}» проставлена ${r.updated} компаниям` : `Группа снята у ${r.updated} компаний`)
        : `Источников ${b.action === "enable_sources" ? "включено" : "выключено"}: ${r.updated} — уйдёт в LF в течение минуты` });
    },
    onError: (e: any) => notifications.show({ color: "red", message: e.message }),
  });
  const askDisable = () => {
    const total = items.filter((c) => sel.includes(c.id)).reduce((a, c) => a + (c.sources || 0), 0);
    if (window.confirm(`Выключить все источники выбранных компаний (${total} шт)? Закупка по ним остановится.`)) bulk.mutate({ action: "disable_sources" });
  };
  return (
    <>
      <Group justify="space-between" mb={4}>
        <Title order={2}>Компании</Title>
        <Button size="xs" leftSection={<IconPlus size={14} />} onClick={() => setOpen(true)}>Добавить статус</Button>
      </Group>
      <AddStatus opened={open} onClose={() => setOpen(false)} onDone={bust} />
      <Text c="dimmed" size="sm" mb="md">откуда взяты номера-источники · статусы приходят по вебхуку или ставятся вручную кнопкой выше · группа уходит в выгрузку контактов, включите колонку в Интеграциях · стоимость = (куплено × цена покупки + куплено × цена обработки) / лидов; цены — в Настройках ({money(q.data?.contact_cost)} + {money(q.data?.handling_cost)})</Text>
      {!!sel.length && (
        <Paper p="xs" mb="xs" withBorder>
          <Group gap="xs" align="flex-end">
            <Text size="sm" fw={600}>Выбрано: {sel.length}</Text>
            <Button size="xs" variant="light" color="red" loading={bulk.isPending} onClick={askDisable}>выключить все источники</Button>
            <Button size="xs" variant="light" color="green" loading={bulk.isPending} onClick={() => bulk.mutate({ action: "enable_sources" })}>включить</Button>
            <Group gap={4} align="flex-end">
              <Autocomplete size="xs" w={220} label="группа" placeholder="название или выберите" data={q.data?.groups || []} value={group} onChange={setGroup} />
              <Button size="xs" variant="light" loading={bulk.isPending} onClick={() => bulk.mutate({ action: "group", group })}>задать</Button>
              <Button size="xs" variant="subtle" loading={bulk.isPending} onClick={() => bulk.mutate({ action: "group", group: "" })}>убрать</Button>
            </Group>
            <Button size="xs" variant="subtle" onClick={() => setSel([])}>снять выбор</Button>
          </Group>
        </Paper>
      )}

      <Paper p="xs" style={{ overflowX: "auto" }}>
        <Table fz="xs" verticalSpacing={5} className="compact">
          <Table.Thead><Table.Tr>
            <Table.Th w={28}><Checkbox size="xs" checked={!!items.length && sel.length === items.length} indeterminate={!!sel.length && sel.length < items.length}
              onChange={(e) => setSel(e.currentTarget.checked ? items.map((c) => c.id) : [])} /></Table.Th>
            <Table.Th>Компания</Table.Th><Table.Th>Группа</Table.Th><Table.Th ta="right">Источников</Table.Th><Table.Th ta="right">Куплено</Table.Th><Table.Th ta="right">Лидов</Table.Th><Table.Th ta="right">Неуспешных</Table.Th><Table.Th ta="right">Квал-лидов</Table.Th><Table.Th ta="right">Конв. лид</Table.Th><Table.Th ta="right">Конв. квал</Table.Th><Table.Th ta="right">Потрачено</Table.Th><Table.Th ta="right">₽ / лид</Table.Th><Table.Th ta="right">₽ / квал</Table.Th>
          </Table.Tr></Table.Thead>
          <Table.Tbody>
            {items.map((c) => (
              <Table.Tr key={c.id}>
                <Table.Td><Checkbox size="xs" checked={sel.includes(c.id)} onChange={(e) => setSel(e.currentTarget.checked ? [...sel, c.id] : sel.filter((x) => x !== c.id))} /></Table.Td>
                <Table.Td>{c.name}</Table.Td>
                <Table.Td>{c.group || <Text span c="dimmed">—</Text>}</Table.Td>
                <Table.Td className="num" ta="right">{n(c.sources)}</Table.Td><Table.Td className="num" ta="right">{n(c.contacts)}</Table.Td>
                <Table.Td className="num" ta="right">{n(c.leads)}</Table.Td><Table.Td className="num" ta="right">{n(c.unsuccessful)}</Table.Td><Table.Td className="num" ta="right">{n(c.quals)}</Table.Td>
                <Table.Td className="num" ta="right">{pct(c.conversion_lead)}</Table.Td><Table.Td className="num" ta="right">{pct(c.conversion_qual)}</Table.Td>
                <Table.Td className="num" ta="right">{money(c.spend)}</Table.Td><Table.Td className="num" ta="right">{money(c.cost_per_lead)}</Table.Td><Table.Td className="num" ta="right">{money(c.cost_per_qual)}</Table.Td>
              </Table.Tr>
            ))}
            {!items.length && <Table.Tr><Table.Td colSpan={13}><Text c="dimmed" ta="center">компаний нет — они появляются из строк «номер;компания» при добавлении источников</Text></Table.Td></Table.Tr>}
          </Table.Tbody>
        </Table>
      </Paper>
    </>
  );
}
