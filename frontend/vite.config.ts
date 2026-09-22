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
        // is not a function"). Started as the previous object form's
        // vendor/charts/ui grouping; charts and lucide-react have since been
        // dropped from it (below).
        manualChunks(id: string) {
          // Normalize Windows separators so one pattern covers both.
          const nid = id.split('\\').join('/')
          if (!nid.includes('/node_modules/')) return undefined
          if (/\/node_modules\/(react|react-dom|react-router-dom)\//.test(nid)) return 'vendor'
          // recharts/d3 are deliberately NOT forced into a named chunk.
          // Doing so made them a *shared* chunk, which rolldown then hoisted
          // into the entry's static imports — index.html modulepreloaded
          // charts-*.js on every route, including the login page, where no
          // chart renders. Measured: 891,879 raw / 251,676 gzip eager before,
          // 502,199 / 142,305 after (-44% / -43%).
          //
          // Left to natural chunking, recharts follows the three lazy pages
          // that import it (Overview / SuiteDetail / ValueMetrics) and splits
          // into AreaChart / BarChart / CartesianChart chunks that load only
          // on those routes — and total *less* than the forced chunk did
          // (391,686 vs 529,975 raw), because only what is used gets included.
          //
          // The same rule holds for the VIZ-103 chart engines: echarts, zrender
          // and three are NEVER named here. Each ECharts chart type is its own
          // dynamic import (components/charts/engines/registry.ts) and
          // `npm run check:bundle` fails if any of them reaches an eager chunk.
          //
          // lucide-react is no longer named either — the same defect at a
          // smaller scale. The named `ui` chunk held EVERY icon any page
          // imports, and because the TopBar/Sidebar icons made it shared with
          // the entry it was modulepreloaded on every route: ~13.6 KB gzip of
          // mostly lazy-page icons on the critical path. Left to natural
          // chunking, each icon follows the chunk that uses it. Measured on
          // Viz Epic 3: eager 179,776 -> 172,698 gzip.
          if (/\/node_modules\/(@radix-ui\/react-dialog)\//.test(nid)) return 'ui'
          return undefined
        },
      },
    },
  },
})
