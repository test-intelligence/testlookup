import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  server: {
    port: 3000,
    proxy: {
      // VITE_PROXY_TARGET overrides the default for in-container dev (compose
      // sets it to http://backend:8000). Plain `npm run dev` on the host falls
      // back to localhost.
      '/api': { target: process.env.VITE_PROXY_TARGET || 'http://localhost:8000', changeOrigin: true, ws: true },
      '/webhooks': { target: process.env.VITE_PROXY_TARGET || 'http://localhost:8000', changeOrigin: true },
      '/ws': { target: process.env.VITE_PROXY_TARGET || 'http://localhost:8000', changeOrigin: true, ws: true },
    },
  },
  build: {
    outDir: 'dist',
    // Disable sourcemaps in production to avoid shipping original source to
    // browsers; keep them in dev for debuggability.
    sourcemap: process.env.NODE_ENV !== 'production',
    rollupOptions: {
      output: {
        // vite 8 bundles with rolldown, which only accepts the function form
        // of manualChunks (the object form fails the build with "manualChunks
        // is not a function"). Same vendor/charts/ui grouping as the previous
        // object form.
        manualChunks(id: string) {
          // Normalize Windows separators so one pattern covers both.
          const nid = id.split('\\').join('/')
          if (!nid.includes('/node_modules/')) return undefined
          if (/\/node_modules\/(react|react-dom|react-router-dom)\//.test(nid)) return 'vendor'
          if (/\/node_modules\/(recharts|d3)\//.test(nid)) return 'charts'
          if (/\/node_modules\/(lucide-react|@radix-ui\/react-dialog)\//.test(nid)) return 'ui'
          return undefined
        },
      },
    },
  },
})
