import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import http from 'node:http'

// Path-based split: requests under /api/nn/* and POST /api/imports go to
// the Python loader; everything else under /api falls through to Node.
// Production uses the same split via the Node `web` service, which proxies
// the same set of paths upstream via $LOADER_URL.
const NODE_URL = process.env.NODE_URL || 'http://localhost:3000'
const LOADER_URL = process.env.LOADER_URL || 'http://localhost:8001'

function _routeToLoader(method: string | undefined, url: string | undefined): boolean {
  if (!url) return false
  if (url.startsWith('/api/nn/')) return true
  if (method === 'POST' && url.startsWith('/api/imports')) return true
  if (method === 'POST' && url.startsWith('/api/aggregate')) return true
  return false
}

function loaderProxyPlugin(): Plugin {
  const loader = new URL(LOADER_URL)
  return {
    name: 'loader-proxy',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (_routeToLoader(req.method, req.url)) {
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
