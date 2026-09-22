import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

/**
 * 开发时前端在 9920，Python 后端在 9921，/api 代理过去。
 * 打包部署时由后端直接托管 dist，本来就是同源，不需要这段。
 * 新增接口的前缀必须落在 /api 下，否则 dev 时代理不到。
 */
const BACKEND = process.env.PORTAL_BACKEND || 'http://127.0.0.1:9921'

// ws: true 是给门户代理用的（/api/proxy/<卡片 id>，见 app/proxy.py）：
// 被代理的内网系统里带 web 终端、实时日志的不少，不转 WebSocket 的话 dev 下那些页面
// 会一直卡在「连接中」，而部署态是同源、压根没有这一层，查起来会以为是后端的问题。
// vite 自己的 HMR 走的是另一个路径，不受这条影响
const proxy = {
  '/api': { target: BACKEND, changeOrigin: false, ws: true }
}

export default defineConfig({
  base: './',
  plugins: [vue()],
  server: { host: true, port: 9920, strictPort: true, proxy },
  preview: { host: true, port: 9920, strictPort: true, proxy }
})
