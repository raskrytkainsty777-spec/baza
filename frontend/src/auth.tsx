import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { Button, Center, Paper, PasswordInput, Stack, Text, Title } from "@mantine/core";
import { api, getToken, setToken } from "./api";

export function RequireAuth({ children }: { children: JSX.Element }) {
  const loc = useLocation();
  if (!getToken()) return <Navigate to="/login" state={{ from: loc.pathname }} replace />;
  return children;
}

export function Login() {
  const nav = useNavigate();
  const loc = useLocation() as any;
  const [token, setTok] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      await api("/auth/me", { token: token.trim() });
      setToken(token.trim());
      nav(loc.state?.from || "/", { replace: true });
    } catch (ex: any) {
      setErr(ex?.status === 401 ? "Токен не подошёл" : ex?.message || "Ошибка");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Center h="100vh" bg="gray.0">
      <Paper w={380} shadow="sm">
        <form onSubmit={submit}>
          <Stack gap="md">
            <div>
              <Title order={3}>baza</Title>
              <Text size="sm" c="dimmed">Вход по токену доступа. Он хранится в этом браузере.</Text>
            </div>
            <PasswordInput
              label="Токен"
              placeholder="ADMIN_TOKEN из настроек сервера"
              value={token}
              onChange={(e) => setTok(e.currentTarget.value)}
              error={err || undefined}
              autoFocus
            />
            <Button type="submit" loading={busy} disabled={!token.trim()}>Войти</Button>
          </Stack>
        </form>
      </Paper>
    </Center>
  );
}
