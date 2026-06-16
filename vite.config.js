import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/login':              { target: 'http://localhost:5002', changeOrigin: true },
      '/users':              { target: 'http://localhost:5002', changeOrigin: true },
      '/config':             { target: 'http://localhost:5002', changeOrigin: true },
      '/stream':             { target: 'http://localhost:5002', changeOrigin: true },
      '/thickness':          { target: 'http://localhost:5002', changeOrigin: true },
      '/download':           { target: 'http://localhost:5002', changeOrigin: true },
      '/db':                 { target: 'http://localhost:5002', changeOrigin: true },
      '/server':             { target: 'http://localhost:5002', changeOrigin: true },
      '/sensors':            { target: 'http://localhost:5002', changeOrigin: true },
      '/socket.io': {
        target: 'http://localhost:5002',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})