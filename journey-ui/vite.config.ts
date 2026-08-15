import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

// UI-only dev server. All /api/* calls proxy to the existing FastAPI app (port 8899)
// — the backend is never touched, this React app just consumes its endpoints.
export default defineConfig({
  // Public base path — the app is served behind the gateway at /demo/life.
  // Dev server ignores this (runs at root:5173); it only affects the built asset URLs.
  base: "/demo/life/",
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8899", changeOrigin: true },
    },
  },
});
