import { reactive, watch } from 'vue'
import { uid, normalizeUrl, takeOverKey } from './utils'

/**
 * 数据保存在浏览器 localStorage，按登录用户分开存：
 * 同一台机器换个人登录，看到的是自己那份导航。
 *
 * 以后想把导航也搬到后端，只要把 read / write 换成接口请求即可，其余代码不用动。
 */
const THEME_KEY = 'portal-nav-theme'   // 主题单独存一份，登录页也要用
const keyFor = (username) => 'portal-nav:' + username

/* 改叫 Portal 之前的老键名，登录时按顺序接管，接完就删，老用户无感 */
const OLD_KEY = (username) => 'home-nav:' + username
const OLD_ANON_KEY = 'home-nav'        // 更早、还没加登录时那份公共数据
const OLD_THEME_KEY = 'home-nav-theme'

takeOverKey(OLD_THEME_KEY, THEME_KEY)  // 主题要在下面 normalize 读它之前先接过来

let key = ''
let stopWatch = null
let timer = null

function read(k) {
  try {
    const raw = localStorage.getItem(k)
    if (raw) return normalize(JSON.parse(raw))
  } catch (e) {
    console.warn('本地数据读取失败，使用默认数据', e)
  }
  return normalize(null)
}

function write(k, data) {
  try {
    localStorage.setItem(k, JSON.stringify(data))
  } catch (e) {
    console.error('保存失败', e)
  }
}

/** 补齐缺失字段，老数据或手改过的 JSON 也不会让页面炸掉 */
function normalize(data) {
  const d = data || {}
  return {
    title: d.title || '我的门户',
    theme: d.theme || localStorage.getItem(THEME_KEY) || 'auto',
    items: (Array.isArray(d.items) ? d.items : defaultItems()).map((i) => ({
      id: i.id || uid(),
      name: i.name || '未命名',
      url: normalizeUrl(i.url),
      desc: i.desc || '',
      icon: i.icon || '',
      sso: (i.sso || '').trim()          // 业务系统的 client_id，留空就是普通跳转
    }))
  }
}

function defaultItems() {
  return [
    { id: uid(), name: 'GitHub', url: 'https://github.com', desc: '代码托管', icon: '' },
    { id: uid(), name: 'Vue 文档', url: 'https://cn.vuejs.org', desc: '官方文档', icon: '' }
  ]
}

export const state = reactive(normalize(null))

function apply(next) {
  state.title = next.title
  state.theme = next.theme
  state.items.splice(0, state.items.length, ...next.items)
}

/** 登录成功后调用：切到这个用户的数据，并开始自动保存 */
export function bindUser(username) {
  stopWatch?.()
  key = keyFor(username)

  // 先认这个人自己的老数据，再认加登录之前那份公共数据（第一个登录的人把它接过来）
  takeOverKey(OLD_KEY(username), key)
  takeOverKey(OLD_ANON_KEY, key)

  apply(read(key))
  stopWatch = watch(state, () => {
    clearTimeout(timer)
    timer = setTimeout(() => write(key, state), 200)
  }, { deep: true })
}

/** 登出：停掉自动保存并清空内存里的数据，别让下一个登录的人看见 */
export function unbindUser() {
  stopWatch?.()
  stopWatch = null
  clearTimeout(timer)
  key = ''
  state.items.splice(0, state.items.length)
  state.title = '我的门户'
}

export function addItem(payload) {
  state.items.push({
    id: uid(),
    name: payload.name.trim(),
    url: normalizeUrl(payload.url),
    desc: (payload.desc || '').trim(),
    icon: (payload.icon || '').trim(),
    sso: (payload.sso || '').trim()
  })
}

export function updateItem(id, payload) {
  const item = state.items.find((i) => i.id === id)
  if (!item) return
  item.name = payload.name.trim()
  item.url = normalizeUrl(payload.url)
  item.desc = (payload.desc || '').trim()
  item.icon = (payload.icon || '').trim()
  item.sso = (payload.sso || '').trim()
}

export function removeItem(id) {
  const i = state.items.findIndex((x) => x.id === id)
  if (i > -1) state.items.splice(i, 1)
}

/** 拖拽排序 */
export function moveItem(from, to) {
  if (to < 0 || to >= state.items.length || from === to) return
  const [it] = state.items.splice(from, 1)
  state.items.splice(to, 0, it)
}

export function exportJson() {
  return JSON.stringify(state, null, 2)
}

export function importJson(text) {
  const next = normalize(JSON.parse(text))
  state.title = next.title
  state.items.splice(0, state.items.length, ...next.items)
}

export function applyTheme() {
  const dark = state.theme === 'dark' ||
    (state.theme === 'auto' && matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
}

watch(() => state.theme, (theme) => {
  localStorage.setItem(THEME_KEY, theme)
  applyTheme()
}, { immediate: true })

matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if (state.theme === 'auto') applyTheme()
})
