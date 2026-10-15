import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  // public/ only holds the recorded session, so only replay builds (VITE_REPLAY=1) ship it.
  publicDir: process.env.VITE_REPLAY === "1" ? "public" : false,
  server: {
    host: "0.0.0.0",
    port: 28735,
    proxy: {
      "/api": "http://localhost:28734",
    },
  },
});
