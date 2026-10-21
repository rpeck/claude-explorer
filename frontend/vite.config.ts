import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        // The e2e config sets this to its own backend (playwright.config.ts).
        target: process.env.CLAUDE_EXPLORER_API_URL ?? 'http://localhost:8765',
        changeOrigin: true,
      },
    },
  },
})