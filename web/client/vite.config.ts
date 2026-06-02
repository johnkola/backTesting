import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import http from 'node:http'

// Method-aware split for /api/imports: POST goes to the Python loader, GET
// (and everything else) falls through to the standard /api proxy below
// (Node, :3000). Production uses the same split via the Node `web` service,
// which proxies POST upstream via $LOADER_URL.
const NODE_URL = process.env.NODE_URL || 'http://localhost:3000'
const LOADER_URL = process.env.LOADER_URL || 'http://localhost:8001'

function loaderProxyPlugin(): Plugin {
  const loader = new URL(LOADER_URL)
  return {
    name: 'loader-proxy',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (req.method === 'POST' && req.url?.startsWith('/api/imports')) {
          const proxied = http.request(
            {
              hostname: loader.hostname,
              port: loader.port || (loader.protocol === 'https:' ? 443 : 80),
              method: req.method,
              path: req.url,
              headers: req.headers,
            },
            (upstream) => {
              res.statusCode = upstream.statusCode ?? 502
              for (const [k, v] of Object.entries(upstream.headers)) {
                if (v !== undefined) res.setHeader(k, v as string | string[])
              }
              upstream.pipe(res)
            },
          )
          proxied.on('error', (err) => {
            res.statusCode = 502
            res.end(`loader proxy error: ${err.message}`)
          })
          req.pipe(proxied)
          return
        }
        next()
      })
    },
  }
}

export default defineConfig({
  plugins: [loaderProxyPlugin(), react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': NODE_URL,
    },
  },
})
