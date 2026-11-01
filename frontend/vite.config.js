import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// 开发时把 /api 代理到后端，避免跨域
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
