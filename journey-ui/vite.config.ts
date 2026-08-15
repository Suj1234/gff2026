import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

// DEV-ONLY (apply: "serve"): let bare /demo/life load the SPA WITHOUT a trailing slash,
// matching prod (FastAPI serves both "" and "/"). We internally rewrite the request URL to
// the slashed base so Vite's own SPA middleware serves index.html; the browser URL stays
// bare (internal rewrite, not a redirect). Never runs in build/prod — prod is FastAPI+dist.
const bareBaseFallback: Plugin = {
  name: "bare-base-fallback",
  apply: "serve",
  configureServer(server) {
    server.middlewares.use((req, _res, next) => {
      if (req.url === "/demo/life") req.url = "/demo/life/";
      next();
    });
  },
};

// UI-only dev server. All /api/* calls proxy to the existing FastAPI app (port 8899)
// — the backend is never touched, this React app just consumes its endpoints.
export default defineConfig({
  // Public base path — the app is served behind the gateway at /demo/life.
  // Dev server ignores this (runs at root:5173); it only affects the built asset URLs.
  base: "/demo/life/",
  plugins: [bareBaseFallback, react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    port: 5173,
    proxy: {
      // The app is served under base /demo/life/, so its fetch("/api/..") resolves to
      // /demo/life/api/.. in the browser (matches prod, where FastAPI is mounted there).
      // Locally the backend has no prefix, so strip /demo/life before forwarding.
      "/demo/life/api": {
        target: "http://127.0.0.1:8899",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/demo\/life/, ""),
      },
      // Keep the bare /api mapping too (direct calls, non-based dev).
      "/api": { target: "http://127.0.0.1:8899", changeOrigin: true },
    },
  },
});
