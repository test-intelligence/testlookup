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
        manualChunks: {
          vendor: ['react', 'react-dom', 'react-router-dom'],
          charts: ['recharts', 'd3'],
          ui: ['lucide-react', '@radix-ui/react-dialog'],
        },
      },
    },
  },
})
