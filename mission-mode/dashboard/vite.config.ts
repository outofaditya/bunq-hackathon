import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/chat": "http://localhost:8000",
      "/events": { target: "http://localhost:8000", changeOrigin: true, ws: false },
      "/stt": "http://localhost:8000",
      "/tts": "http://localhost:8000",
      "/signin": "http://localhost:8000",
      "/bunq-webhook": "http://localhost:8000",
      "/simulate-approve": "http://localhost:8000",
      "/mock-vendor": "http://localhost:8000",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
