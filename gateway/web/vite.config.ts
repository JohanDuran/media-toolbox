import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` proxies the API to a running gateway, for working on the dashboard.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8765" } },
});
