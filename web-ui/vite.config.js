import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      // 开发时把 /api 转发给 FastAPI，生产环境由 nginx 承担这个角色
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
