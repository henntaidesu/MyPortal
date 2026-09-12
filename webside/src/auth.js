/** 登录状态。页面起来先问一次后端，之后由登录/登出维护。 */
import { reactive } from 'vue'
import { api, OfflineError } from './api'

export const auth = reactive({
  user: null,        // { username, display_name, email, roles }
  ready: false,      // 已经问过后端了，没问完之前别闪登录页
  offline: false,    // 后端连不上
  clients: []        // 已注册的业务系统，给卡片的下拉框用
})

export async function refresh() {
  try {
    const data = await api('/api/me')
    auth.user = data.user
    auth.offline = false
    loadClients()
  } catch (err) {
    auth.user = null
    auth.offline = err instanceof OfflineError
  } finally {
    auth.ready = true
  }
}

export async function login(username, password) {
  const data = await api('/api/login', { method: 'POST', body: { username, password } })
  auth.user = data.user
  auth.offline = false
  loadClients()
  return data.user
}

export async function logout() {
  try {
    await api('/api/logout', { method: 'POST' })
  } finally {
    auth.user = null
    auth.clients = []
  }
}

export async function changePassword(oldPassword, newPassword) {
  await api('/api/password', {
    method: 'POST',
    body: { old_password: oldPassword, new_password: newPassword }
  })
}

async function loadClients() {
  try {
    const data = await api('/api/sso/clients')
    auth.clients = data.clients
  } catch {
    auth.clients = []   // 拿不到就退化成手填 client_id，不影响用
  }
}

/**
 * 登录后要跳回的地址。
 * 只认站内路径：允许完整地址的话，这个参数就成了给钓鱼用的开放重定向。
 */
export function safeNext(raw) {
  const next = (raw || '').trim()
  if (!next.startsWith('/') || next.startsWith('//')) return ''
  return next
}
