import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      '/api': 'http://127.0.0.1:9001',
      '/health': 'http://127.0.0.1:9001',
      '/jobs': {
        target: 'http://127.0.0.1:9001',
        bypass(req) {
          if (req.method === 'GET') return req.url
        },
      },
      '/ws': { target: 'ws://127.0.0.1:9001', ws: true },
    },
  },
  preview: {
    port: 5174,
    strictPort: true,
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
