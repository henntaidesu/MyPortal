import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

/**
 * 开发时前端在 9920，Python 后端在 9921，/api 代理过去。
 * 打包部署时由后端直接托管 dist，本来就是同源，不需要这段。
 * 新增接口的前缀必须落在 /api 下，否则 dev 时代理不到。
 */
const BACKEND = process.env.PORTAL_BACKEND || 'http://127.0.0.1:9921'

const proxy = {
  '/api': { target: BACKEND, changeOrigin: false }
}

export default defineConfig({
  base: './',
  plugins: [vue()],
  server: { host: true, port: 9920, strictPort: true, proxy },
  preview: { host: true, port: 9920, strictPort: true, proxy }
})
