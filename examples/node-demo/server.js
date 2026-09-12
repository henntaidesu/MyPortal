/**
 * 业务系统接入示例（Node，零依赖，只用内置模块）。
 *
 * 跑起来：
 *     node examples/node-demo/server.js
 * 先在 server 目录注册它：
 *     python manage.py addclient demo-node --name "Node 示例" ^
 *         --redirect-uri http://127.0.0.1:8802/sso/callback ^
 *         --logout-uri  http://127.0.0.1:8802/sso/logout-notify ^
 *         --home-url    http://127.0.0.1:8802/
 * 把打印出来的 client_secret 填到下面 CLIENT_SECRET。
 *
 * 用 Express 的话，把下面三个 handler 换成三个路由就行，逻辑一模一样。
 */
const http = require('node:http')
const crypto = require('node:crypto')

// ------------------------------------------------------------------ 配置
const SSO_BASE = process.env.SSO_BASE || 'http://127.0.0.1:9920'
const SELF_BASE = process.env.SELF_BASE || 'http://127.0.0.1:8802'
const CLIENT_ID = process.env.CLIENT_ID || 'demo-node'
const CLIENT_SECRET = process.env.CLIENT_SECRET || '把 addclient 打印的 secret 填这里'

const COOKIE = 'demo_node_session'
const CALLBACK = SELF_BASE + '/sso/callback'

/** 本地会话。真实项目换 Redis；一定要存 sso_sid，单点登出靠它找人 */
const sessions = new Map()

const readCookie = (req, name) =>
  (req.headers.cookie || '')
    .split(';')
    .map((s) => s.trim().split('='))
    .find(([k]) => k === name)?.[1] || ''

const currentUser = (req) => sessions.get(readCookie(req, COOKIE))?.user || null

const redirect = (res, to, extraHeaders = {}) => {
  res.writeHead(302, { Location: to, ...extraHeaders })
  res.end()
}

const send = (res, status, body, type = 'text/html; charset=utf-8') => {
  res.writeHead(status, { 'content-type': type })
  res.end(body)
}

function readBody(req) {
  return new Promise((resolve) => {
    let raw = ''
    req.on('data', (chunk) => {
      raw += chunk
      if (raw.length > 10_000) req.destroy()
    })
    req.on('end', () => {
      try {
        resolve(JSON.parse(raw || '{}'))
      } catch {
        resolve({})
      }
    })
  })
}

/** 定长比较，避免用比较耗时去猜 secret */
function secretOk(given) {
  const a = Buffer.from(String(given || ''))
  const b = Buffer.from(CLIENT_SECRET)
  return a.length === b.length && crypto.timingSafeEqual(a, b)
}

// ------------------------------------------------------------------ 路由

const routes = {
  // 1. 没登录就去认证中心要票
  'GET /sso/login': (req, res) => {
    const query = new URLSearchParams({
      client_id: CLIENT_ID,
      redirect_uri: CALLBACK,
      state: crypto.randomBytes(8).toString('hex')
    })
    redirect(res, `${SSO_BASE}/sso/authorize?${query}`)
  },

  // 2. 拿票换身份，然后建自己的会话
  'GET /sso/callback': async (req, res, url) => {
    const ticket = url.searchParams.get('ticket')
    if (!ticket) return send(res, 400, '缺少 ticket')

    // 关键：这一步是后端对后端，client_secret 绝不能出现在浏览器里
    const resp = await fetch(`${SSO_BASE}/sso/validate`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ client_id: CLIENT_ID, client_secret: CLIENT_SECRET, ticket })
    })
    if (!resp.ok) return send(res, 401, `票据校验失败：${await resp.text()}`)

    const data = await resp.json()
    const localSid = crypto.randomBytes(32).toString('base64url')
    sessions.set(localSid, { user: data.user, ssoSid: data.sid })

    redirect(res, '/', {
      'Set-Cookie': `${COOKIE}=${localSid}; HttpOnly; SameSite=Lax; Path=/`
    })
  },

  // 3. 主页退出时会通知过来，把对应的本地会话销毁
  'POST /sso/logout-notify': async (req, res) => {
    const body = await readBody(req)
    if (!secretOk(body.secret)) return send(res, 403, 'forbidden')

    for (const [localSid, session] of sessions) {
      if (session.ssoSid === body.sid) sessions.delete(localSid)
    }
    send(res, 200, JSON.stringify({ ok: true }), 'application/json')
  },

  'GET /logout': (req, res) => {
    sessions.delete(readCookie(req, COOKIE))
    redirect(res, '/', { 'Set-Cookie': `${COOKIE}=; Max-Age=0; Path=/` })
  },

  'GET /logout-all': (req, res) => {
    sessions.delete(readCookie(req, COOKIE))
    redirect(res, `${SSO_BASE}/sso/logout?client_id=${encodeURIComponent(CLIENT_ID)}`, {
      'Set-Cookie': `${COOKIE}=; Max-Age=0; Path=/`
    })
  },

  'GET /': (req, res) => {
    const user = currentUser(req)
    if (!user) return redirect(res, '/sso/login')
    send(res, 200, `<!doctype html><meta charset="utf-8">
<title>Node 示例系统</title>
<style>body{font:15px/1.8 system-ui,"Microsoft YaHei",sans-serif;padding:48px;max-width:680px}
code{background:#f2f4f7;padding:2px 6px;border-radius:4px}</style>
<h2>这里是「Node 示例系统」</h2>
<p>当前用户：<b>${user.display_name}</b>（${user.username}）</p>
<p>角色：<code>${user.roles.join(', ') || '无'}</code></p>
<p>整个过程没在这个系统里输过账号密码。</p>
<p><a href="/logout">退出本系统</a> ｜ <a href="/logout-all">退出所有系统</a></p>`)
  }
}

http
  .createServer(async (req, res) => {
    const url = new URL(req.url, SELF_BASE)
    const handler = routes[`${req.method} ${url.pathname}`]
    if (!handler) return send(res, 404, 'not found')
    try {
      await handler(req, res, url)
    } catch (err) {
      console.error(err)
      if (!res.headersSent) send(res, 500, '服务器错误')
    }
  })
  .listen(8802, '127.0.0.1', () => console.log(`示例系统跑在 ${SELF_BASE}`))
