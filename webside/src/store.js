import { reactive, watch } from 'vue'
import { api } from './api'
import { uid, normalizeUrl, takeOverKey } from './utils'

/**
 * 导航数据存在 MySQL 里（nav 表，每人一行、整份 JSON），换台机器登进来就能看到自己那份。
 * localStorage 退成缓存，不再是权威：
 *
 *   - 进页面先把缓存画出来，不用等接口回来，省掉一次空白；
 *   - 后端连不上时照常能看能改，改动攒在本机，等下次同步再推上去；
 *   - 本机有还没推上去的改动就打一个「脏」标记，bindUser 时先推后拉，别被服务端那份冲掉。
 *
 * 和后端打交道的只有 pull() / push() 两个函数，其余代码不关心数据从哪儿来——
 * 这是这个文件刻意保持的边界。冲突策略是后写的盖先写的，两台机器同时改，后按下的那边赢。
 */
const THEME_KEY = 'portal-nav-theme'   // 主题单独存一份，还没登录的登录页也要用
const keyFor = (id) => 'portal-nav:u' + id
const NAME_KEY = (username) => 'portal-nav:' + username   // 按用户名分区的那版老键
const DIRTY_KEY = (k) => k + ':dirty'  // 有这个键 = 本机的改动还没推上去

/* 改叫 Portal 之前的老键名，登录时按顺序接管，接完就删，老用户无感 */
const OLD_KEY = (username) => 'home-nav:' + username
const OLD_ANON_KEY = 'home-nav'        // 更早、还没加登录时那份公共数据
const OLD_THEME_KEY = 'home-nav-theme'

takeOverKey(OLD_THEME_KEY, THEME_KEY)  // 主题要在下面 normalize 读它之前先接过来

const LOCAL_DELAY = 200                // 本机缓存写得勤一点，反正不花钱
const PUSH_DELAY = 800                 // 推后端的攒一下，别拖一下卡片发十几个请求

let key = ''
let stopWatch = null
let localTimer = null
let pushTimer = null

/** 同步状态，页面上拿它提示「现在只存在本机」 */
export const sync = reactive({
  offline: false,        // 上一次和后端说话失败了
  saving: false          // 正在往后端写
})

function readLocal(k) {
  try {
    const raw = localStorage.getItem(k)
    if (raw) return JSON.parse(raw)
  } catch (e) {
    console.warn('本机缓存读不出来，忽略', e)
  }
  return null            // null = 本机没有缓存，和「缓存里是一份空导航」不是一回事
}

function writeLocal(k, data) {
  try {
    localStorage.setItem(k, JSON.stringify(data))
  } catch (e) {
    console.error('写本机缓存失败', e)
  }
}

function markDirty() {
  try {
    localStorage.setItem(DIRTY_KEY(key), '1')
  } catch { /* 存不下也就算了，大不了这次改动只活在内存里 */ }
}

function isDirty() {
  return localStorage.getItem(DIRTY_KEY(key)) !== null
}

/** 推给后端。成功才清掉脏标记——没清掉的话下次登录会先推再拉 */
async function push() {
  sync.saving = true
  try {
    await api('/api/nav', {
      method: 'PUT',
      body: { data: { title: state.title, theme: state.theme, items: state.items } }
    })
    localStorage.removeItem(DIRTY_KEY(key))
    sync.offline = false
  } catch (e) {
    sync.offline = true
    console.warn('导航没能存到后端，先留在本机', e)
    throw e
  } finally {
    sync.saving = false
  }
}

/** 从后端取。返回拿到的数据；后端还没有这个人的数据就返回 null */
async function pull() {
  const res = await api('/api/nav')
  sync.offline = false
  return res.data
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

/**
 * 登录成功后调用：把这个人的数据装进来，并开始自动保存。
 * 先画本机缓存再跟后端对账，所以它 await 不 await 都不会白屏。
 */
export async function bindUser(user) {
  stopWatch?.()
  stopWatch = null
  clearTimeout(localTimer)
  clearTimeout(pushTimer)

  const nameKey = NAME_KEY(user.username)
  // id 是后端 /api/me 给的；万一没拿到就退回按用户名存，总比一份都不存强
  key = user.id == null ? nameKey : keyFor(user.id)

  // 本机老键一站站往新键上搬：home-nav:<名字> → portal-nav:<名字> → portal-nav:u<id>。
  // 先认这个人自己的老数据，再认加登录之前那份公共数据（第一个登录的人把它接过来）
  takeOverKey(OLD_KEY(user.username), nameKey)
  takeOverKey(OLD_ANON_KEY, nameKey)
  if (key !== nameKey) takeOverKey(nameKey, key)

  const cached = readLocal(key)
  // 有缓存就先画出来；没有就先空着，别让默认的示例卡片闪一下又被后端那份换掉
  if (cached) apply(normalize(cached))
  else state.items.splice(0, state.items.length)

  await reconcile(cached)

  stopWatch = watch(state, () => {
    markDirty()                        // 先打标记：这会儿关掉页面，下次登录还认得出本机更新
    clearTimeout(localTimer)
    localTimer = setTimeout(() => writeLocal(key, state), LOCAL_DELAY)
    clearTimeout(pushTimer)
    pushTimer = setTimeout(() => push().catch(() => {}), PUSH_DELAY)
  }, { deep: true })
}

/** 和后端对账：本机有没推上去的改动就先推，否则以后端那份为准 */
async function reconcile(cached) {
  try {
    if (cached && isDirty()) {
      await push()                     // 上回没推成（或者推到一半关了页面），本机这份更新
      return
    }
    const remote = await pull()
    if (remote) {
      apply(normalize(remote))
      writeLocal(key, state)
      return
    }
    // 后端还没有这个人的数据
    if (cached) {
      await push()                     // 老用户：把本机那份搬上去，这是一次性的迁移
      writeLocal(key, state)
    } else {
      apply(normalize(null))           // 新用户：给两张示例卡片，改了之后自然会推上去
    }
  } catch (e) {
    sync.offline = true
    console.warn('导航数据没同步上，先用本机这份', e)
    if (!cached) apply(normalize(null))
  }
}

/** 退出登录前调用：把攒着的改动落下去，别跟着会话一起丢了 */
export async function flushNav() {
  if (!key) return
  clearTimeout(localTimer)
  clearTimeout(pushTimer)
  writeLocal(key, state)
  if (!isDirty()) return
  try {
    await push()
  } catch { /* 推不上去就留着脏标记，下次登录再说 */ }
}

/** 登出：停掉自动保存并清空内存里的数据，别让下一个登录的人看见 */
export function unbindUser() {
  stopWatch?.()
  stopWatch = null
  clearTimeout(localTimer)
  clearTimeout(pushTimer)
  key = ''
  sync.offline = false
  sync.saving = false
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

/* 关页面前把缓存补一刀：推后端那一下来不及发，但脏标记还在，下次登录会先推上去 */
addEventListener('beforeunload', () => {
  if (key) writeLocal(key, state)
})
