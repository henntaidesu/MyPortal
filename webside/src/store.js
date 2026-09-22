import { reactive, watch } from 'vue'
import { api } from './api'
import { auth } from './auth'
import { uid, normalizeUrl, takeOverKey } from './utils'

/**
 * 导航数据存在后端的 MySQL 里（见 app/navstore.py），**每个账号一份**，
 * 换台机器、换个浏览器登进来都是自己那份。
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
/**
 * 本机缓存的键**按账号分**：`portal-nav:u<用户 id>`。
 *
 * 多用户之前这里是一个固定的 `portal-nav`。继续用固定键的话，同一台电脑上
 * 换个账号登进来，先画出来的是**上一个人的导航**——而且页面一起来就把它当成
 * 「本机这份」推给后端，等于拿 A 的导航盖掉 B 的。
 *
 * 没登录时退回那个不带 id 的老键：登录页上用不着缓存，但 `readLocal` 在
 * 登录之前也可能被叫到，给个确定的值比返回 undefined 省事。
 */
const BASE_KEY = 'portal-nav'
const THEME_KEY = 'portal-nav-theme'   // 主题单独存一份，首屏脚本要在 Vue 起来之前读它

function navKey() {
  const id = auth.user?.id
  return id ? `${BASE_KEY}:u${id}` : BASE_KEY
}

function dirtyKey() {
  return navKey() + ':dirty'           // 有这个键 = 本机的改动还没推上去
}

/* 更早那两个匿名键直接认领。单账号那一版留下的 `portal-nav` 不在这儿迁——
   那会儿它属于谁说不清，认领给第一个登进来的人是错的。它会在第一次
   `boot()` 里按「当前这个人还没有自己的缓存」认领一次，见下面 readLocal */
takeOverKey('home-nav-theme', THEME_KEY)   // 主题要在下面 normalize 读它之前先接过来
takeOverKey('home-nav', BASE_KEY)

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
    const raw = localStorage.getItem(navKey())
    if (raw) return JSON.parse(raw)
    /* 这个账号还没有自己的缓存。单账号那一版留下的 `portal-nav` 里可能正躺着
       这个人自己的导航（他就是当年那个唯一的账号），认领过来一次。
       认领**只发生一次**：认完就搬到带 id 的键下，老键删掉，
       第二个人登进来时那儿已经什么都没有了 */
    if (auth.user?.id) {
      takeOverKey(BASE_KEY, navKey())
      const moved = localStorage.getItem(navKey())
      if (moved) return JSON.parse(moved)
    }
  } catch (e) {
    console.warn('本机缓存读不出来，忽略', e)
  }
  return null            // null = 本机没有缓存，和「缓存里是一份空导航」不是一回事
}

function writeLocal() {
  try {
    localStorage.setItem(navKey(), JSON.stringify(state))
  } catch (e) {
    console.error('写本机缓存失败', e)
  }
}

function markDirty() {
  try {
    localStorage.setItem(dirtyKey(), '1')
  } catch { /* 存不下也就算了，大不了这次改动只活在内存里 */ }
}

function isDirty() {
  return localStorage.getItem(dirtyKey()) !== null
}

/** 推给后端。成功才清掉脏标记——没清掉的话下次进页面会先推再拉 */
async function push() {
  sync.saving = true
  try {
    await api('/api/nav', {
      method: 'PUT',
      body: { data: { title: state.title, theme: state.theme, groups: state.groups } }
    })
    localStorage.removeItem(dirtyKey())
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

/**
 * 门户代理那一栏有三挡（见 app/proxy.py）：
 *
 *   false    关
 *   true     开，直接转发——只转卡片这一台机器，正文一个字不改。内网后台用这个
 *   'site'   开，整站改写——一张卡片转一整族域名，还会改写页面里的地址。
 *            公网站点（メルカリ、ヤフオク 这些）图和接口散在别的域名上，得用这个
 *
 * 存下去的就是这三个值，后端照着它分路。老数据里是布尔值，认得出来。
 */
function proxyMode(v) {
  return v === 'site' ? 'site' : !!v
}

function normalizeItem(i) {
  return {
    id: i.id || uid(),
    name: i.name || '未命名',
    url: normalizeUrl(i.url),
    desc: i.desc || '',
    icon: i.icon || '',
    // 开了就不直接连 url，改走 /api/proxy/<id>，由门户那个进程转出去（见 app/proxy.py）。
    // 后端的 navstore.clean 不管卡片有哪些字段，所以加字段只用改这儿
    proxy: proxyMode(i.proxy),
    // 整站模式下额外放行的域名（逗号隔开）。站点自己那几个域名后端有一份内置的
    // （app/proxy_webside/），这里填的是补充
    proxyHosts: typeof i.proxyHosts === 'string' ? i.proxyHosts : '',
    // Cookie 代理：开了之后上游站点的登录态存在服务器上（app/cookiejar.py），
    // 换设备、清缓存都不掉登录。只有 proxy 开着时才有意义
    cookieJar: !!i.cookieJar
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
    // title 页面上不显示了（左上角那行标题去掉了），但照样读进来、照样写回去——
    // 不然谁在 conf.json 里手写了一个标题，下次存导航就被悄悄抹掉
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

/** 会话没了：停掉自动保存并清空内存里这份，别让登录页背后还留着一屏卡片。
    本机缓存留着不动——它带着用户 id，下次这个人自己登回来还能少等一次接口，
    换个人登进来也看不到它。没落盘的改动也不会丢：脏标记还在 localStorage 里，
    下次这个人登录会先推后拉 */
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

// ------------------------------------------------------------------ 导入

/**
 * 认得出来的几种形状，全都收：
 *
 *   {server, auth, nav: {...}}   整份 conf.json（最常见：把老机器上那份直接拖进来）
 *   {title, theme, groups: []}   只有 nav 那一段
 *   {groups: []} / {items: []}   再省一点
 *   [ {name, items: []} ]        光一个分类数组
 *   [ {name, url} ]              光一串卡片（老的扁平结构）
 *
 * 认得越松越好：人手里那份 JSON 是从哪儿抠出来的说不准，为形状不对而拒收，
 * 只会让人回去自己拼一个外层壳子。
 */
function pickNav(raw) {
  if (Array.isArray(raw)) {
    if (raw.some((x) => x && Array.isArray(x.items))) return { groups: raw }
    if (raw.some((x) => x && typeof x.url === 'string')) return { items: raw }
    return null
  }
  if (!raw || typeof raw !== 'object') return null
  // 整份 conf.json。nav 是 null 的时候（还没配过的机器）当作没有，别当成一份空导航
  if (raw.nav && typeof raw.nav === 'object') return raw.nav
  if (Array.isArray(raw.groups) || Array.isArray(raw.items)) return raw
  return null
}

/** 一段文本 → 可以导入的那份。认不出来就抛一句人话 */
export function parseImport(text) {
  let raw
  try {
    raw = JSON.parse(text)
  } catch (e) {
    throw new Error('这不是一份能读的 JSON：' + e.message)
  }
  return fromObject(raw)
}

export function fromObject(raw) {
  const nav = pickNav(raw)
  if (!nav) {
    throw new Error('这份数据里没有导航：认 conf.json 整份、认 nav 那一段、'
      + '也认一个 groups 或 items 数组')
  }
  const normalized = normalize(nav)
  const items = normalized.groups.reduce((n, g) => n + g.items.length, 0)
  if (!items && !normalized.groups.length) throw new Error('这份数据里一个分类、一张卡片都没有')
  return {
    nav: normalized,
    // title / theme 只在对方**真的写了**的时候才盖掉现有的：谁在 conf.json 里
    // 手写过一个标题，不该被一次导入悄悄抹掉（和 normalize() 那条注释同一个道理）
    hasTitle: typeof nav.title === 'string' && !!nav.title,
    hasTheme: typeof nav.theme === 'string' && !!nav.theme,
    groups: normalized.groups.length,
    items
  }
}

/**
 * 把解析好的那份并进来。改的是 state，所以存盘走的还是那条自动保存的路——
 * 导入不另开一条跟后端说话的通道（store.js 的边界就这一条）。
 *
 * `mode = 'merge'`：同名分类并进去，**同一个分类里地址重复的跳过**；没有的分类追加。
 *                   卡片 id 撞上现有的就换一个新的。
 * `mode = 'replace'`：整棵树换掉，导入那份的 id 原样留着——这样「导出再导回来」
 *                   能接上原来的 Cookie 罐子（罐子钉在卡片 id 上，见 app/cookiejar.py）。
 */
export function importNav(parsed, mode = 'merge') {
  const incoming = parsed.nav
  if (parsed.hasTitle) state.title = incoming.title
  if (parsed.hasTheme) state.theme = incoming.theme

  if (mode === 'replace') {
    state.groups.splice(0, state.groups.length, ...incoming.groups)
    return { groups: parsed.groups, items: parsed.items, skipped: 0, replaced: true }
  }

  // 现有的全部 id。导入的 id 撞上任何一个都要换掉：撞了的话拖拽、编辑会认错对象，
  // 而且 Cookie 罐子是钉在卡片 id 上的，会串到另一张卡片去
  const used = new Set()
  for (const g of state.groups) {
    used.add(g.id)
    for (const i of g.items) used.add(i.id)
  }
  const fresh = (id) => {
    let next = id
    while (!next || used.has(next)) next = uid()
    used.add(next)
    return next
  }

  let addedGroups = 0
  let addedItems = 0
  let skipped = 0
  for (const g of incoming.groups) {
    let target = state.groups.find((x) => x.name.trim() === g.name.trim())
    if (!target) {
      target = { id: fresh(g.id), name: g.name, items: [] }
      state.groups.push(target)
      target = state.groups[state.groups.length - 1]
      addedGroups++
    }
    const have = new Set(target.items.map((i) => i.url))
    for (const item of g.items) {
      if (have.has(item.url)) {
        skipped++            // 同一个分类里已经有这个地址了，不重复添加
        continue
      }
      target.items.push({ ...item, id: fresh(item.id) })
      have.add(item.url)
      addedItems++
    }
  }
  return { groups: addedGroups, items: addedItems, skipped, replaced: false }
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
    icon: (payload.icon || '').trim(),
    proxy: proxyMode(payload.proxy),
    proxyHosts: (payload.proxyHosts || '').trim(),
    cookieJar: !!payload.cookieJar
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
  item.proxy = proxyMode(payload.proxy)
  item.proxyHosts = (payload.proxyHosts || '').trim()
  item.cookieJar = !!payload.cookieJar

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

// ------------------------------------------------------------------ 主题
// 页面上没有切换按钮了，主题跟着 conf.json 里的 nav.theme 走，默认 auto = 跟随系统。
// 不 export：只有这个文件自己用。首屏那一下由 index.html 里的内联脚本负责，避免闪白。

function applyTheme() {
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
