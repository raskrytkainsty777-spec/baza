import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// В бою фронт и API на одном origin (nginx). Для `npm run dev` проксируем
// /api на боевой сервер — локальной базы у нас нет и не нужно.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://95.81.103.196", changeOrigin: true } },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 1200 },
});
