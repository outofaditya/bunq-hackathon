import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      "/events": "http://localhost:8000",
      "/health": "http://localhost:8000",
      "/state": "http://localhost:8000",
      "/missions": "http://localhost:8000",
      "/tts": "http://localhost:8000",
      "/assets": "http://localhost:8000",
      "/bunq-webhook": "http://localhost:8000",
      "/mock-restaurant": "http://localhost:8000",
      "/mock-hotel": "http://localhost:8000",
      "/mock-subscriptions": "http://localhost:8000",
    },
  },
});
