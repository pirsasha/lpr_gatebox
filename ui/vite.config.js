import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import dns from "node:dns";

dns.setDefaultResultOrder("ipv4first");

export default defineConfig({
  plugins: [react()],

  build: {
    modulePreload: false,
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        manualChunks: undefined,
      },
    },
  },

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