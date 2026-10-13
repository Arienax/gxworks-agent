import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Production is served by the Python loopback server. Development still enforces
// one browser origin; API requests retain the backend's configured origin.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
        configure(proxy) {
          proxy.on("proxyReq", (proxyReq, request) => {
            if (
              ["http://127.0.0.1:5173", "http://localhost:5173"].includes(
                request.headers.origin || "",
              )
            )
              proxyReq.setHeader("origin", "http://127.0.0.1:8765");
          });
        },
      },
    },
  },
});
