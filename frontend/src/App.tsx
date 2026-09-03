import { BrowserRouter, NavLink as RouterLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { AppShell, Badge, Box, Group, NavLink, ScrollArea, Text, UnstyledButton } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import {
  IconBriefcase, IconBuildingCommunity, IconLayoutDashboard, IconListDetails, IconLogout,
  IconNotebook, IconSearch, IconSettings, IconUsers,
} from "@tabler/icons-react";
import { Login, RequireAuth } from "./auth";
import { api, clearToken } from "./api";
import Dashboard from "./pages/Dashboard";
import Donors from "./pages/Donors";
import Posts from "./pages/Posts";
import { Cities, CityPage } from "./pages/Cities";
import Search from "./pages/Search";
import Jobs from "./pages/Jobs";
import Settings from "./pages/Settings";

const NAV = [
  { to: "/", label: "Мастер задач", icon: IconLayoutDashboard, end: true },
  { to: "/search", label: "Поиск доноров", icon: IconSearch },
  { to: "/donors", label: "Доноры", icon: IconUsers },
  { to: "/posts", label: "Посты", icon: IconListDetails },
  { to: "/cities", label: "Города", icon: IconBuildingCommunity },
  { to: "/jobs", label: "Задания и журнал", icon: IconBriefcase },
  { to: "/settings", label: "Настройки", icon: IconSettings },
];

function StateStrip() {
  const ops = useQuery({ queryKey: ["ops-status"], queryFn: () => api("/ops/status"), refetchInterval: 15_000 });
  const o = ops.data;
  if (!o) return null;
  const dead = Object.values(o.workers || {}).filter((w: any) => !w.alive).length;
  const pill = (on: boolean, label: string) => <Badge size="sm" variant="light" color={on ? "green" : "gray"} style={{ textTransform: "none" }}>{label}: {on ? "вкл" : "выкл"}</Badge>;
  return (
    <Group gap="xs" mb="md" component={RouterLink} to="/settings" style={{ textDecoration: "none" }}>
      {pill(o.collection_enabled, "посты и заведение доноров")}
      {pill(o.comments_enabled, "сбор комментариев")}
      {pill(o.ai_enabled, "ИИ")}
      <Badge size="sm" variant="light" color={dead ? "red" : "green"} style={{ textTransform: "none" }}>воркеры: {dead ? `${dead} молчат` : "все живы"}</Badge>
      <Badge size="sm" variant="light" color="grape" style={{ textTransform: "none" }}>parser.im: {o.parserim.lines_busy}/{o.parserim.lines_total} строк · очередь {o.parserim.queued}</Badge>
      <Text size="xs" c="dimmed">переключатели — в Настройках</Text>
    </Group>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  const loc = useLocation();
  const nav = useNavigate();
  return (
    <AppShell navbar={{ width: 220, breakpoint: "sm" }} padding="lg" bg="gray.0">
      <AppShell.Navbar p="sm" bg="white">
        <Group px="xs" py="sm" gap="xs">
          <Box w={26} h={26} bg="violet.6" style={{ borderRadius: 7, display: "grid", placeItems: "center" }}>
            <Text c="white" fw={700} size="sm">b</Text>
          </Box>
          <div>
            <Text fw={600} size="sm" lh={1.1}>baza</Text>
            <Text size="xs" c="dimmed" lh={1.1}>комментарии в лиды</Text>
          </div>
        </Group>
        <ScrollArea style={{ flex: 1 }} mt="xs">
          {NAV.map((i) => {
            const active = i.end ? loc.pathname === i.to : loc.pathname.startsWith(i.to);
            return (
              <NavLink key={i.to} component={RouterLink} to={i.to} label={i.label}
                leftSection={<i.icon size={18} stroke={1.6} />} active={active}
                variant="light" style={{ borderRadius: 8 }} />
            );
          })}
        </ScrollArea>
        <UnstyledButton onClick={() => { clearToken(); nav("/login"); }} px="sm" py="xs">
          <Group gap="xs"><IconLogout size={16} stroke={1.6} /><Text size="sm" c="dimmed">Выйти</Text></Group>
        </UnstyledButton>
        <Text size="xs" c="dimmed" px="sm" pb="xs">
          <IconNotebook size={12} style={{ verticalAlign: -2 }} /> v0.3 · {new Date().toLocaleDateString("ru-RU")}
        </Text>
      </AppShell.Navbar>
      <AppShell.Main><StateStrip />{children}</AppShell.Main>
    </AppShell>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/*" element={
          <RequireAuth>
            <Shell>
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/search" element={<Search />} />
                <Route path="/donors" element={<Donors />} />
                <Route path="/posts" element={<Posts />} />
                <Route path="/cities" element={<Cities />} />
                <Route path="/cities/:id" element={<CityPage />} />
                <Route path="/jobs" element={<Jobs />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="*" element={<Dashboard />} />
              </Routes>
            </Shell>
          </RequireAuth>
        } />
      </Routes>
    </BrowserRouter>
  );
}
