import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev: vite on :5173 proxies the API to the FastAPI server on :8450.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8450',
    },
  },
})
