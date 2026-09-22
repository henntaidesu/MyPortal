/**
 * 登录状态。页面起来先问一次后端，之后由登录 / 登出维护。
 *
 * 多用户了，所以 `auth.user` 里比以前多了几样东西：
 *
 *   { id, username, display_name, role, is_admin }
 *
 * **`id` 是 store.js 用来分本机缓存的**（每个账号一份，见那边的 KEY），
 * 所以这个字段不能省——省了的话同一台电脑上换个账号登进来，
 * 看到的会是上一个人的导航。
 *
 * `is_admin` 只用来决定「用户管理」那个入口画不画。**它不是权限**：
 * 真正的拦截在后端（app/routers/users.py 的 require_admin），
 * 前端这份只是别让人对着一个注定 403 的按钮点。
 */
import { reactive } from 'vue'
import { api, OfflineError, setUnauthorizedHandler } from './api'

export const auth = reactive({
  user: null,              // { id, username, display_name, role, is_admin } | null
  ready: false,            // 已经问过后端了，没问完之前别闪登录页
  offline: false,          // 后端连不上
  /* 后端开着哪几条登录路。登录页拿它决定画什么：
     两条都开就是「表单 + 一个单点登录按钮」，只开 OIDC 就只有那个按钮。
     默认给 local: true，是为了后端连不上时至少还画得出表单 */
  providers: { local: true, oidc: { enabled: false, label: '单点登录' } }
})

function accept(data) {
  auth.user = data.user
  auth.offline = false
}

/** 登录页要用的那几条路。失败就用默认值，不往上抛——问不到不该让登录页白屏 */
export async function loadProviders() {
  try {
    const data = await api('/api/auth/providers')
    auth.providers = {
      local: data.local !== false,
      oidc: data.oidc || { enabled: false, label: '单点登录' }
    }
  } catch {
    /* 保持默认值 */
  }
}

export async function refresh() {
  try {
    accept(await api('/api/me'))
  } catch (err) {
    auth.user = null
    auth.offline = err instanceof OfflineError
  } finally {
    auth.ready = true
  }
  // 没登录才需要知道有哪几条登录路。登着的时候问这个纯属多一个请求
  if (!auth.user) await loadProviders()
}

export async function login(username, password) {
  accept(await api('/api/login', { method: 'POST', body: { username, password } }))
  return auth.user
}

export async function logout() {
  try {
    await api('/api/logout', { method: 'POST' })
  } finally {
    // 请求失败也要退回登录页：Cookie 可能已经不算数了，留在面板上只会
    // 让人对着一个存不上的页面接着改
    auth.user = null
    await loadProviders()
  }
}

/** 跳去 IdP。整页跳转，不是 fetch——OIDC 那一路要浏览器自己走完 */
export function oidcLogin() {
  const next = location.pathname + location.search + location.hash
  location.href = '/api/auth/oidc/login?next=' + encodeURIComponent(next || '/')
}

/* 会话过期时（后端回 401）把人退回登录页，别让他对着一个存不上的面板接着改。
   注册在这里而不是 App.vue：store.js 里的自动保存也会撞上 401 */
setUnauthorizedHandler(() => {
  auth.user = null
})
