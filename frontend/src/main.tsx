import React from "react";
import ReactDOM from "react-dom/client";
import { MantineProvider, createTheme } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@mantine/core/styles.css";
import "@mantine/dates/styles.css";
import "@mantine/notifications/styles.css";
import "./styles.css";
import App from "./App";

// Ориентир — Mixpanel: светлый фон, фиолетовый акцент, аккуратные карточки.
const theme = createTheme({
  primaryColor: "violet",
  defaultRadius: "md",
  fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif",
  fontFamilyMonospace: "ui-monospace, 'Cascadia Mono', Consolas, monospace",
  headings: { fontWeight: "600" },
  components: {
    Table: { defaultProps: { highlightOnHover: true, verticalSpacing: "xs", horizontalSpacing: "sm" } },
    Paper: { defaultProps: { withBorder: true, p: "md" } },
  },
});

const qc = new QueryClient({
  defaultOptions: { queries: { staleTime: 15_000, retry: 1, refetchOnWindowFocus: false } },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="light">
      <Notifications position="top-right" />
      <QueryClientProvider client={qc}>
        <App />
      </QueryClientProvider>
    </MantineProvider>
  </React.StrictMode>,
);
