import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["icons/icon-192.svg", "icons/icon-512.svg", "icons/favicon.svg"],
      manifest: {
        id: "/app/",
        name: "Clorofilla",
        short_name: "Clorofilla",
        description: "Riconoscimento piante e consigli di cura",
        theme_color: "#1e7a56",
        background_color: "#f2efe6",
        display: "standalone",
        start_url: "/app/",
        scope: "/app/",
        icons: [
          // SVG icons
          {
            src: "icons/icon-192.svg",
            sizes: "192x192",
            type: "image/svg+xml",
            purpose: "any"
          },
          {
            src: "icons/icon-512.svg",
            sizes: "512x512",
            type: "image/svg+xml",
            purpose: "any maskable"
          },
          // PNG icons for iOS/Android
          {
            src: "icons/ios/icon_120.png",
            sizes: "120x120",
            type: "image/png"
          },
          {
            src: "icons/ios/icon_152.png",
            sizes: "152x152",
            type: "image/png"
          },
          {
            src: "icons/ios/icon_167.png",
            sizes: "167x167",
            type: "image/png"
          },
          {
            src: "icons/ios/icon_180.png",
            sizes: "180x180",
            type: "image/png"
          },
          {
            src: "icons/ios/icon_1024.png",
            sizes: "1024x1024",
            type: "image/png"
          }
        ]
      },
      workbox: {
        globPatterns: ["**/*.{js,css,html,svg,png}"]
      }
    })
  ]
});
