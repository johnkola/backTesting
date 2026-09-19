import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The client is a standalone SPA: it has no backend of its own and reaches the
// API by URL. In production that URL is baked in at build time via
// VITE_API_URL (docker compose passes it as a build arg).
//
// In dev, leaving VITE_API_URL unset makes the client issue same-origin
// relative requests, which this proxy forwards to the API service. That keeps
// the dev server free of CORS entirely. Set VITE_API_URL to point the dev
// client straight at a running API instead — the API sends CORS headers, so
// that works too.
const API_URL = process.env.API_URL || 'http://localhost:8001'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      // One upstream now: the API service fans out to the loader and the
      // engine itself, so the dev server no longer needs to know that split.
      '/api': API_URL,
    },
  },
})
