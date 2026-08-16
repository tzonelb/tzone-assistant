import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      // The canonical chart of accounts is shared with the backend seed, so both sides
      // create identical rows and converge on first sync.
      "@shared": fileURLToPath(new URL("../shared", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    host: "127.0.0.1",
    fs: { allow: [".."] },
  },
});
