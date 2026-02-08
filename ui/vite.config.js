// ui/vite.config.js
// LPR_GATEBOX UI PATCH v0.2.0 | updated 2026-02-01
//
// Что изменено:
// 1) IPv4-first (лечит internalConnectMultiple/::1 на Windows)
// 2) Proxy на backend:
//    - /api/*  -> http://127.0.0.1:8080
//    - /health -> http://127.0.0.1:8080/health
//    - /reload -> http://127.0.0.1:8080/reload
//
// Зачем:
// - UI в браузере ходит на localhost:5173
// - Vite проксирует на backend:8080, без CORS и без плясок с адресами.

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import dns from "node:dns";

dns.setDefaultResultOrder("ipv4first");

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8080",
        changeOrigin: true,
        secure: false,
      },
      "/health": {
        target: "http://127.0.0.1:8080",
        changeOrigin: true,
        secure: false,
      },
      "/reload": {
        target: "http://127.0.0.1:8080",
        changeOrigin: true,
        secure: false,
      },
    },
  },
});
