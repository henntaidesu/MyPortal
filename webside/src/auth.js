/**
 * 登录状态。页面起来先问一次后端，之后由登录 / 登出维护。
 *
 * 只有一个账号，认的是后端 conf.json 里那对用户名口令，所以这里没有角色、
 * 没有用户列表、也没有「改密码」——要改口令去编辑 conf.json 然后重启。
 */
import { reactive } from 'vue'
import { api, OfflineError, setUnauthorizedHandler } from './api'

export const auth = reactive({
  user: null,              // { username } | null
  ready: false,            // 已经问过后端了，没问完之前别闪登录页
  offline: false           // 后端连不上
})

function accept(data) {
  auth.user = data.user
  auth.offline = false
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
}

export async function login(username, password) {
  accept(await api('/api/login', { method: 'POST', body: { username, password } }))
  return auth.user
}

/* 页面上没有「退出」按钮，所以这里也没有 logout()。
   后端 /api/logout 还在，要把按钮加回来时对着它写一个就行。 */

/* 会话过期时（后端回 401）把人退回登录页，别让他对着一个存不上的面板接着改。
   注册在这里而不是 App.vue：store.js 里的自动保存也会撞上 401 */
setUnauthorizedHandler(() => {
  auth.user = null
})
