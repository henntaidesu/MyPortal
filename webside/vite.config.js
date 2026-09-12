import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

/**
 * 开发时前端在 9920，认证后端在 9921。
 * /api 和 /sso 都代理到后端，浏览器看到的仍是同源，Cookie 才能正常带上。
 * 打包部署时由后端直接托管 dist，本来就是同源，不需要这段。
 */
const BACKEND = process.env.SSO_BACKEND || 'http://127.0.0.1:9921'

const proxy = {
  '/api': { target: BACKEND, changeOrigin: false },
  '/sso': { target: BACKEND, changeOrigin: false }
}

export default defineConfig({
  base: './',
  plugins: [vue()],
  server: { host: true, port: 9920, strictPort: true, proxy },
  preview: { host: true, port: 9920, strictPort: true, proxy }
})
