import { useEffect, useState } from "react";
import { Link as RouterLink, Navigate, Route, Routes, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { AppShell, Badge, Box, Button, Center, Group, NavLink, Paper, PasswordInput, ScrollArea, Stack, Text, TextInput, Title, UnstyledButton } from "@mantine/core";
import { IconBan, IconBuilding, IconDatabase, IconListDetails, IconLogout, IconSettings, IconUsersGroup } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { cabApi, cabToken, clearCabToken, setCabToken } from "./api";
import { n } from "../ui";
import Sources from "./pages/Sources";
import Dosbor from "./pages/Dosbor";
import Companies from "./pages/Companies";
import Base from "./pages/Base";
import Blacklist from "./pages/Blacklist";
import CabSettings from "./pages/CabSettings";

const NAV = [
  { to: "/cabinet", label: "Источники", icon: IconListDetails, end: true },
  { to: "/cabinet/companies", label: "Компании", icon: IconBuilding },
  { to: "/cabinet/base", label: "База", icon: IconDatabase },
  { to: "/cabinet/blacklist", label: "Чёрный список", icon: IconBan },
  { to: "/cabinet/dosbor", label: "Досбор", icon: IconUsersGroup },
  { to: "/cabinet/settings", label: "Настройки", icon: IconSettings },
];

export function useMe() {
  return useQuery({ queryKey: ["cab-me"], queryFn: () => cabApi("/me"), refetchInterval: 60_000 });
}

function CabLogin() {
  const nav = useNavigate();
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setErr("");
    try {
      const r = await cabApi("/auth/login", { method: "POST", body: { login, password } });
      setCabToken(r.token); nav("/cabinet", { replace: true });
    } catch (ex: any) { setErr(ex.message || "Ошибка"); } finally { setBusy(false); }
  };
  return (
    <Center h="100vh" bg="gray.0">
      <Paper w={380} shadow="sm">
        <form onSubmit={submit}>
          <Stack gap="md">
            <div><Title order={3}>Кабинет закупки</Title><Text size="sm" c="dimmed">Вход для клиентов</Text></div>
            <TextInput label="Логин" value={login} onChange={(e) => setLogin(e.currentTarget.value)} autoFocus />
            <PasswordInput label="Пароль" value={password} onChange={(e) => setPassword(e.currentTarget.value)} error={err || undefined} />
            <Button type="submit" loading={busy} disabled={!login || !password}>Войти</Button>
          </Stack>
        </form>
      </Paper>
    </Center>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  const loc = useLocation();
  const nav = useNavigate();
  const me = useMe();
  const m = me.data;
  return (
    <AppShell navbar={{ width: 220, breakpoint: "sm" }} padding="lg" bg="gray.0">
      <AppShell.Navbar p="sm" bg="white">
        <Group px="xs" py="sm" gap="xs">
          <Box w={26} h={26} bg="teal.6" style={{ borderRadius: 7, display: "grid", placeItems: "center" }}><Text c="white" fw={700} size="sm">г</Text></Box>
          <div><Text fw={600} size="sm" lh={1.1}>{m?.name || "кабинет"}</Text><Text size="xs" c="dimmed" lh={1.1}>закупка номеров</Text></div>
        </Group>
        <ScrollArea style={{ flex: 1 }} mt="xs">
          {NAV.map((i) => {
            const path = loc.pathname.replace(/\/+$/, "") || "/";
            const active = i.end ? path === i.to : path.startsWith(i.to);
            return <NavLink key={i.to} component={RouterLink} to={i.to} label={i.label} leftSection={<i.icon size={18} stroke={1.6} />} active={active} variant="light" color="teal" style={{ borderRadius: 8 }} />;
          })}
        </ScrollArea>
        <UnstyledButton onClick={() => { clearCabToken(); nav("/cabinet/login"); }} px="sm" py="xs">
          <Group gap="xs"><IconLogout size={16} stroke={1.6} /><Text size="sm" c="dimmed">Выйти</Text></Group>
        </UnstyledButton>
      </AppShell.Navbar>
      <AppShell.Main>
        {m && (
          <Group gap="xs" mb="md">
            <Badge size="lg" variant="light" color={m.balance_contacts ? "teal" : "red"} style={{ textTransform: "none" }}>
              баланс: {m.balance_contacts == null ? "—" : n(m.balance_contacts)} контактов{m.balance_rub != null ? ` · ${n(m.balance_rub)} ₽` : ""}
            </Badge>
            <Badge size="lg" variant="light" color={m.lf_status === "active" ? "green" : "gray"} style={{ textTransform: "none" }}>закупка: {m.lf_status === "active" ? "идёт" : m.lf_status || "не запущена"}</Badge>
            {m.lf_error && <Badge size="lg" variant="light" color="red" style={{ textTransform: "none" }}>{m.lf_error.slice(0, 80)}</Badge>}
            <Text size="xs" c="dimmed">обновление баланса раз в минуту</Text>
          </Group>
        )}
        {children}
      </AppShell.Main>
    </AppShell>
  );
}

function RequireCab({ children }: { children: JSX.Element }) {
  if (!cabToken()) return <Navigate to="/cabinet/login" replace />;
  return children;
}

export default function CabinetApp() {
  const [sp] = useSearchParams();
  const nav = useNavigate();
  useEffect(() => {
    const t = sp.get("token");
    if (t) { setCabToken(t); nav("/cabinet", { replace: true }); }
  }, [sp, nav]);
  return (
    <Routes>
      <Route path="login" element={<CabLogin />} />
      <Route path="*" element={
        <RequireCab>
          <Shell>
            <Routes>
              <Route path="/" element={<Sources />} />
              <Route path="companies" element={<Companies />} />
              <Route path="base" element={<Base />} />
              <Route path="blacklist" element={<Blacklist />} />
              <Route path="dosbor" element={<Dosbor />} />
              <Route path="settings" element={<CabSettings />} />
              <Route path="*" element={<Sources />} />
            </Routes>
          </Shell>
        </RequireCab>
      } />
    </Routes>
  );
}
