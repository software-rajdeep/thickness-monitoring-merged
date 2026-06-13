import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Proxy API requests to backend on port 5000
      '/login':              { target: 'http://localhost:5000', changeOrigin: true },
      '/users':              { target: 'http://localhost:5000', changeOrigin: true },
      '/config':             { target: 'http://localhost:5000', changeOrigin: true },
      '/stream':             { target: 'http://localhost:5000', changeOrigin: true },
      '/thickness':          { target: 'http://localhost:5000', changeOrigin: true },
      '/download':           { target: 'http://localhost:5000', changeOrigin: true },
      '/db':                 { target: 'http://localhost:5000', changeOrigin: true },
      '/server':             { target: 'http://localhost:5000', changeOrigin: true },
      '/sensors':            { target: 'http://localhost:5000', changeOrigin: true },
      '/socket.io': {
        target: 'http://localhost:5000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})