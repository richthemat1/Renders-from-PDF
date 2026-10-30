import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "./",
  // One bundle read from disk by the application window, never downloaded: its size does not slow the start.
  build: { target: "es2022", sourcemap: false, chunkSizeWarningLimit: 800 },
});
