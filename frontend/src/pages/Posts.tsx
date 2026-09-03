import { useState } from "react";
import { Anchor, Badge, Button, Group, Paper, Select, Table, Text, TextInput, Title, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, qs } from "../api";
import { Kpi, KpiRow, StatusBadge, cityOptions, dt, n, useCities } from "../ui";

const SHOW = [
  { value: "all", label: "все" }, { value: "new", label: "новые за период" }, { value: "growth", label: "с приростом" },
  { value: "selling", label: "продающие" }, { value: "non_selling", label: "не продающие" },
  { value: "active", label: "в сборе" }, { value: "frozen", label: "замороженные" },
];

export default function Posts() {
  const qc = useQueryClient();
  const cities = useCities();
  const [city, setCity] = useState("");
  const [show, setShow] = useState("all");
  const [hook, setHook] = useState("");
  const [category, setCategory] = useState("");
  const [sort, setSort] = useState("comments");
  const [q, setQ] = useState("");
  const params = { city_id: city, show, hook, category, sort, q, days: 1, limit: 200 };

  const summary = useQuery({ queryKey: ["posts-summary", city], queryFn: () => api(`/posts/summary${qs({ city_id: city })}`) });
  const facets = useQuery({ queryKey: ["facets", city], queryFn: () => api(`/posts/facets${qs({ city_id: city })}`) });
  const list = useQuery({ queryKey: ["posts", params], queryFn: () => api(`/posts${qs(params)}`) });
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: number; body: any }) => api(`/posts/${id}`, { method: "PATCH", body }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["posts"] }); qc.invalidateQueries({ queryKey: ["posts-summary"] }); },
    onError: (e: any) => notifications.show({ color: "red", message: e.message }),
  });

  const s = summary.data;
  const items: any[] = list.data?.items || [];
  const opt = (rows: any[] | undefined, all: string) => [{ value: "", label: all }, ...(rows || []).map((r) => ({ value: r.value, label: `${r.value} (${r.count})` }))];

  return (
    <>
      <Group justify="space-between" mb="md">
        <div><Title order={2}>Посты</Title><Text c="dimmed" size="sm">{list.data ? `${n(list.data.total)} по фильтру` : ""}</Text></div>
        <Select size="xs" w={200} value={city} onChange={(v) => setCity(v || "")} data={cityOptions(cities.data?.cities)} />
      </Group>

      {s && <KpiRow>
        <Kpi value={n(s.new_today)} label="новых постов за сутки" hint={`продающих ${n(s.new_selling_today)}`} />
        <Kpi value={n(s.growth_today)} label="с приростом комментов" />
        <Kpi value={n(s.comments_today)} label="комментов пришло за сутки" />
        <Kpi value={n(s.leads_today)} label="лидов за сутки" />
        <Kpi value={`${n(s.active)} / ${n(s.frozen)}`} label="в сборе / заморожено" hint={`всего ${n(s.total)}`} />
      </KpiRow>}

      <Group mb="sm" gap="xs">
        <Select size="xs" w={170} value={show} onChange={(v) => setShow(v || "all")} data={SHOW} />
        <Select size="xs" w={220} value={hook} onChange={(v) => setHook(v || "")} data={opt(facets.data?.hooks, "любой крючок")} searchable />
        <Select size="xs" w={190} value={category} onChange={(v) => setCategory(v || "")} data={opt(facets.data?.categories, "любая категория")} searchable />
        <Select size="xs" w={170} value={sort} onChange={(v) => setSort(v || "comments")} data={[{ value: "comments", label: "по комментам" }, { value: "delta", label: "по приросту" }, { value: "published", label: "по дате" }]} />
        <TextInput size="xs" w={220} placeholder="в тексте поста…" value={q} onChange={(e) => setQ(e.currentTarget.value)} />
      </Group>

      <Paper style={{ overflowX: "auto" }}>
        <Table>
          <Table.Thead><Table.Tr>
            <Table.Th>Дата</Table.Th><Table.Th>Донор</Table.Th><Table.Th>Текст</Table.Th><Table.Th>Оффер</Table.Th>
            <Table.Th>Крючок</Table.Th><Table.Th>Категория</Table.Th><Table.Th>Призыв · код</Table.Th><Table.Th>Прод.</Table.Th>
            <Table.Th>Просм.</Table.Th><Table.Th>Комм. вчера → сегодня</Table.Th><Table.Th>Собрано / лидов</Table.Th><Table.Th>Монитор</Table.Th><Table.Th />
          </Table.Tr></Table.Thead>
          <Table.Tbody>
            {items.map((p) => (
              <Table.Tr key={p.id}>
                <Table.Td className="num">{dt(p.published_at, false)}{p.product_type === "clips" && <Text size="xs" c="dimmed">рилс</Text>}</Table.Td>
                <Table.Td><span className="mono">{p.username}</span>{p.city_source === "ai" && <Text size="xs" c="grape">город по посту: {p.city}</Text>}</Table.Td>
                <Table.Td><Anchor href={p.url} target="_blank" size="sm" className="clip2" c="dark">{p.caption || "—"}</Anchor></Table.Td>
                <Table.Td><Text size="sm" className="clip" style={{ maxWidth: 240 }}>{p.offer || "—"}</Text></Table.Td>
                <Table.Td><Text size="sm">{p.hook || "—"}</Text></Table.Td>
                <Table.Td><Text size="sm">{p.category || "—"}</Text></Table.Td>
                <Table.Td><Text size="xs">{p.cta_type || "—"}</Text>{p.code_word && <Badge size="xs" variant="outline" mt={2}>{p.code_word}</Badge>}</Table.Td>
                <Table.Td>{p.is_selling === null ? <Badge size="xs" color="yellow" variant="light">ждёт ИИ</Badge> : p.is_selling ? <Badge size="xs" color="green" variant="light">да</Badge> : <Badge size="xs" color="gray" variant="light">нет</Badge>}</Table.Td>
                <Table.Td className="num">{n(p.views)}</Table.Td>
                <Table.Td className="num">{n(p.comments_prev)} → <b>{n(p.comments_count)}</b>{p.delta > 0 && <Text span c="green.8" fw={600}> +{n(p.delta)}</Text>}{p.zero_growth_days > 0 && <Text size="xs" c="dimmed">без прироста {p.zero_growth_days} дн</Text>}</Table.Td>
                <Table.Td className="num">{n(p.collected_comments)} / {n(p.leads)}</Table.Td>
                <Table.Td><StatusBadge kind="post" value={p.monitor_status} /></Table.Td>
                <Table.Td>
                  {(p.monitor_status === "active" || p.monitor_status === "forced") && <Tooltip label="исключить из сбора комментов"><Button size="compact-xs" variant="subtle" color="gray" onClick={() => patch.mutate({ id: p.id, body: { monitor_status: "excluded" } })}>Не брать</Button></Tooltip>}
                  {p.monitor_status === "excluded" && <Button size="compact-xs" variant="subtle" onClick={() => patch.mutate({ id: p.id, body: { monitor_status: "forced" } })}>Брать</Button>}
                  {p.monitor_status === "frozen" && <Button size="compact-xs" variant="subtle" onClick={() => patch.mutate({ id: p.id, body: { monitor_status: "active" } })}>Вернуть</Button>}
                </Table.Td>
              </Table.Tr>
            ))}
            {!items.length && <Table.Tr><Table.Td colSpan={13}><Text c="dimmed" ta="center">{list.isLoading ? "загрузка…" : "по фильтру пусто"}</Text></Table.Td></Table.Tr>}
          </Table.Tbody>
        </Table>
      </Paper>
    </>
  );
}
