import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 28735,
    proxy: {
      "/api": "http://localhost:28734",
    },
  },
});
