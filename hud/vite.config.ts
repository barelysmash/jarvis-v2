import { resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    port: 5173,
    strictPort: true,
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      // Two pages from one build: the HUD (index.html) and the
      // second-screen cockpit (cockpit.html). FastAPI's static mount
      // serves both from the same origin as /ws and /api.
      input: {
        main: resolve(__dirname, "index.html"),
        cockpit: resolve(__dirname, "cockpit.html"),
      },
    },
  },
});
