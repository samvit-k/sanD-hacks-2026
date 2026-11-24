import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// /api is proxied to the FastAPI backend (uvicorn on :8000). Uploads can take minutes,
// so the proxy timeout is longer than the client-side 180 s upload timeout.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://127.0.0.1:8000', timeout: 240000, proxyTimeout: 240000 } },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{js,jsx}'],
  },
})