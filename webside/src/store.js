import { reactive, watch } from 'vue'
import { api } from './api'
import { uid, normalizeUrl, takeOverKey } from './utils'

/**
 * 导航数据存在后端的 conf.json 里（`nav` 那一段，见 app/navstore.py），
 * 换台机器、换个浏览器登进来都是同一份。只有一个账号，所以没有「谁的导航」这回事。
 *
 * 结构是「分类 → 卡片」两层，页面上一个分类画成一张大卡：
 *
 *   { title, theme, groups: [ { id, name, items: [ { id, name, url, desc, icon } ] } ] }
 *
 * localStorage 是缓存，不是权威：
 *
 *   - 进页面先把缓存画出来，不用等接口回来，省掉一次空白；
 *   - 后端连不上时照常能看能改，改动攒在本机，等下次同步再推上去；
 *   - 本机有还没推上去的改动就打一个「脏」标记，下次进来先推后拉，别被服务端那份冲掉。
 *
 * 和后端打交道的只有 pull() / push() 两个函数，其余代码不关心数据从哪儿来——
 * 这是这个文件刻意保持的边界。冲突策略是后写的盖先写的，两个浏览器同时改，后按下的那边赢。
 */
const KEY = 'portal-nav'               // 本机缓存
const DIRTY_KEY = KEY + ':dirty'       // 有这个键 = 本机的改动还没推上去
const THEME_KEY = 'portal-nav-theme'   // 主题单独存一份，首屏脚本要在 Vue 起来之前读它

/* 早先按用户分区的老键（portal-nav:u<id>）认不出是谁的，不迁移；
   更早那两个匿名键倒是能直接认领 */
takeOverKey('home-nav-theme', THEME_KEY)   // 主题要在下面 normalize 读它之前先接过来
takeOverKey('home-nav', KEY)

const LOCAL_DELAY = 200                // 本机缓存写得勤一点，反正不花钱
const PUSH_DELAY = 800                 // 推后端的攒一下，别拖一下卡片发十几个请求

const DEFAULT_GROUP = '我的导航'
const DEFAULT_TITLE = '我的门户'

let stopWatch = null
let localTimer = null
let pushTimer = null

/** 同步状态，页面上拿它提示「现在只存在本机」 */
export const sync = reactive({
  offline: false,        // 上一次和后端说话失败了
  saving: false          // 正在往后端写
})

function readLocal() {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) return JSON.parse(raw)
  } catch (e) {
    console.warn('本机缓存读不出来，忽略', e)
  }
  return null            // null = 本机没有缓存，和「缓存里是一份空导航」不是一回事
}

function writeLocal() {
  try {
    localStorage.setItem(KEY, JSON.stringify(state))
  } catch (e) {
    console.error('写本机缓存失败', e)
  }
}

function markDirty() {
  try {
    localStorage.setItem(DIRTY_KEY, '1')
  } catch { /* 存不下也就算了，大不了这次改动只活在内存里 */ }
}

function isDirty() {
  return localStorage.getItem(DIRTY_KEY) !== null
}

/** 推给后端。成功才清掉脏标记——没清掉的话下次进页面会先推再拉 */
async function push() {
  sync.saving = true
  try {
    await api('/api/nav', {
      method: 'PUT',
      body: { data: { title: state.title, theme: state.theme, groups: state.groups } }
    })
    localStorage.removeItem(DIRTY_KEY)
    sync.offline = false
  } catch (e) {
    sync.offline = true
    console.warn('导航没能存到后端，先留在本机', e)
    throw e
  } finally {
    sync.saving = false
  }
}

/** 从后端取。返回拿到的数据；后端那边 nav 还是空的就返回 null */
async function pull() {
  const res = await api('/api/nav')
  sync.offline = false
  return res.data
}

function normalizeItem(i) {
  return {
    id: i.id || uid(),
    name: i.name || '未命名',
    url: normalizeUrl(i.url),
    desc: i.desc || '',
    icon: i.icon || ''
  }
}

function normalizeGroup(g) {
  return {
    id: g.id || uid(),
    name: g.name || DEFAULT_GROUP,
    items: (Array.isArray(g.items) ? g.items : [])
      .filter((i) => i && typeof i === 'object')
      .map(normalizeItem)
  }
}

/** 补齐缺失字段，老数据或手改过的 conf.json 也不会让页面炸掉 */
function normalize(data) {
  const d = data || {}
  let groups
  if (Array.isArray(d.groups)) {
    groups = d.groups.filter((g) => g && typeof g === 'object').map(normalizeGroup)
  } else if (Array.isArray(d.items)) {
    // 分类是后加的。老数据整份是一个扁平的 items 列表，收进一个默认分类里——
    // 别去掉这条分支，不然老用户升上来会看到一片空
    groups = [normalizeGroup({ name: DEFAULT_GROUP, items: d.items })]
  } else {
    groups = defaultGroups()
  }
  return {
    title: d.title || DEFAULT_TITLE,
    theme: d.theme || localStorage.getItem(THEME_KEY) || 'auto',
    groups
  }
}

function defaultGroups() {
  return [normalizeGroup({
    name: '常用',
    items: [
      { name: 'GitHub', url: 'https://github.com', desc: '代码托管' },
      { name: 'Vue 文档', url: 'https://cn.vuejs.org', desc: '官方文档' }
    ]
  })]
}

export const state = reactive(normalize(null))

function apply(next) {
  state.title = next.title
  state.theme = next.theme
  state.groups.splice(0, state.groups.length, ...next.groups)
}

/** 卡片总数，页面底部那行要用 */
export function totalItems() {
  return state.groups.reduce((n, g) => n + g.items.length, 0)
}

/**
 * 登录成功后调一次：先画本机缓存，再跟后端对账，然后开始自动保存。
 * await 不 await 都不会白屏。
 */
export async function boot() {
  const cached = readLocal()
  // 有缓存就先画出来；没有就先空着，别让默认的示例卡片闪一下又被后端那份换掉
  if (cached) apply(normalize(cached))
  else state.groups.splice(0, state.groups.length)

  await reconcile(cached)

  stopWatch?.()
  stopWatch = watch(state, () => {
    markDirty()                        // 先打标记：这会儿关掉页面，下次进来还认得出本机更新
    clearTimeout(localTimer)
    localTimer = setTimeout(writeLocal, LOCAL_DELAY)
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
      writeLocal()
      return
    }
    // 后端那边 nav 还是空的
    if (cached) {
      await push()                     // 本机这份搬上去，这是一次性的迁移
      writeLocal()
    } else {
      apply(normalize(null))           // 全新的：给一个示例分类，改了之后自然会推上去
    }
  } catch (e) {
    sync.offline = true
    console.warn('导航数据没同步上，先用本机这份', e)
    if (!cached) apply(normalize(null))
  }
}

/** 退出登录前调用：把攒着的改动落下去，别跟着会话一起丢了 */
export async function flushNav() {
  if (!stopWatch) return
  clearTimeout(localTimer)
  clearTimeout(pushTimer)
  writeLocal()
  if (!isDirty()) return
  try {
    await push()
  } catch { /* 推不上去就留着脏标记，下次登录再说 */ }
}

/** 登出：停掉自动保存并清空内存里这份，别让登录页背后还留着一屏卡片。
    本机缓存留着不动——只有一个账号，下次登录还是同一个人，留着能少等一次接口 */
export function unbind() {
  stopWatch?.()
  stopWatch = null
  clearTimeout(localTimer)
  clearTimeout(pushTimer)
  sync.offline = false
  sync.saving = false
  state.groups.splice(0, state.groups.length)
  state.title = DEFAULT_TITLE
}

// ------------------------------------------------------------------ 分类

export function findGroup(gid) {
  return state.groups.find((g) => g.id === gid)
}

export function groupIndex(gid) {
  return state.groups.findIndex((g) => g.id === gid)
}

export function addGroup(name = '') {
  const group = normalizeGroup({ name: name.trim() || '新分类', items: [] })
  state.groups.push(group)
  return group
}

export function renameGroup(gid, name) {
  const group = findGroup(gid)
  if (group) group.name = name.trim() || DEFAULT_GROUP
}

export function removeGroup(gid) {
  const i = groupIndex(gid)
  if (i > -1) state.groups.splice(i, 1)
}

/** 分类之间换位置 */
export function moveGroup(from, to) {
  if (to < 0 || to >= state.groups.length || from === to) return
  const [g] = state.groups.splice(from, 1)
  state.groups.splice(to, 0, g)
}

// ------------------------------------------------------------------ 卡片

export function addItem(gid, payload) {
  const group = findGroup(gid)
  if (!group) return
  group.items.push(normalizeItem({
    name: payload.name.trim(),
    url: payload.url,
    desc: (payload.desc || '').trim(),
    icon: (payload.icon || '').trim()
  }))
}

/** payload.group 填了别的分类就顺手搬过去，编辑弹窗里那个下拉框就是干这个的 */
export function updateItem(gid, id, payload) {
  const from = findGroup(gid)
  const item = from?.items.find((i) => i.id === id)
  if (!item) return
  item.name = payload.name.trim()
  item.url = normalizeUrl(payload.url)
  item.desc = (payload.desc || '').trim()
  item.icon = (payload.icon || '').trim()

  const to = payload.group && payload.group !== gid ? findGroup(payload.group) : null
  if (to) {
    from.items.splice(from.items.indexOf(item), 1)
    to.items.push(item)
  }
}

export function removeItem(gid, id) {
  const group = findGroup(gid)
  if (!group) return
  const i = group.items.findIndex((x) => x.id === id)
  if (i > -1) group.items.splice(i, 1)
}

/**
 * 拖拽：同一个分类里换位置，或者搬到别的分类去。
 * toIndex 给 -1 表示放到目标分类的末尾（拖到分类的空白处就是这种）。
 */
export function moveItem(fromGid, fromIndex, toGid, toIndex) {
  const from = findGroup(fromGid)
  const to = findGroup(toGid)
  if (!from || !to) return
  if (fromIndex < 0 || fromIndex >= from.items.length) return
  if (from === to && (toIndex === fromIndex || toIndex < 0)) return

  const [item] = from.items.splice(fromIndex, 1)
  const at = toIndex < 0 || toIndex > to.items.length ? to.items.length : toIndex
  to.items.splice(at, 0, item)
}

// ------------------------------------------------------------------ 其它

export function exportJson() {
  return JSON.stringify(state, null, 2)
}

export function importJson(text) {
  const next = normalize(JSON.parse(text))
  state.title = next.title
  state.groups.splice(0, state.groups.length, ...next.groups)
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

/* 关页面前把缓存补一刀：推后端那一下来不及发，但脏标记还在，下次进来会先推上去 */
addEventListener('beforeunload', () => {
  if (stopWatch) writeLocal()
})
