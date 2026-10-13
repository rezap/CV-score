import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run build` writes into the Python package, so `cvscore serve` serves the app.
// `npm run dev` proxies API calls to a running `cvscore serve` on port 8000.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../cvscore/web/static", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
