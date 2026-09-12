import { getOrigin } from './utils'

const KEY = 'home-nav-icons'
const TTL = 7 * 24 * 60 * 60 * 1000   // 取到的图标缓存 7 天
const FAIL_TTL = 6 * 60 * 60 * 1000   // 取不到的只记 6 小时，站点临时挂掉不至于长期没图标
const MAX_DATA = 120 * 1024           // 单个图标超过 120KB 就只记地址，不存字节

let cache = {}
try {
  cache = JSON.parse(localStorage.getItem(KEY) || '{}')
} catch {
  cache = {}
}

let saveTimer = null
function persist() {
  clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(cache))
    } catch {
      // localStorage 满了就整个丢掉重来，图标丢了可以再取，不能影响导航数据
      cache = {}
      try { localStorage.removeItem(KEY) } catch {}
    }
  }, 400)
}

/** 自定义图标按图标地址缓存，否则按站点 origin 缓存（同站点只取一次） */
function keyOf(item) {
  const custom = (item.icon || '').trim()
  if (custom && /^(https?:\/\/|\/)/i.test(custom)) return custom
  return getOrigin(item.url)
}

/** 交给服务端解析的地址：自定义图标直接取，否则由服务端把整套 favicon 解析做完 */
function apiOf(item) {
  const custom = (item.icon || '').trim()
  if (custom && /^(https?:\/\/|\/)/i.test(custom)) return '/api/icon?url=' + encodeURIComponent(custom)
  const origin = getOrigin(item.url)
  return origin ? '/api/icon?site=' + encodeURIComponent(origin) : ''
}

/** 代理不可用时的兜底直连地址 */
function fallbackUrls(item) {
  const custom = (item.icon || '').trim()
  if (custom && /^(https?:\/\/|\/)/i.test(custom)) return [custom]
  const origin = getOrigin(item.url)
  return origin ? [origin + '/favicon.ico', origin + '/favicon.png'] : []
}

/* 代理是否可用：打包成静态站点单独部署时没有这个接口，连续取不到就不再尝试 */
let proxyAvailable = null
let proxyMisses = 0

function blobToDataUrl(blob) {
  return new Promise((resolve) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result))
    r.onerror = () => resolve('')
    r.readAsDataURL(blob)
  })
}

async function viaProxy(api) {
  if (proxyAvailable === false) return ''
  try {
    const r = await fetch(api)
    const type = r.headers.get('content-type') || ''
    if (!r.ok || !/^image\//i.test(type)) {
      if (++proxyMisses >= 3 && proxyAvailable !== true) proxyAvailable = false
      return ''
    }
    proxyAvailable = true
    proxyMisses = 0
    const blob = await r.blob()
    if (!blob.size || blob.size > MAX_DATA) return ''
    return await blobToDataUrl(blob)
  } catch {
    proxyAvailable = false
    return ''
  }
}

/** 代理取不到时退回直连，只探测能不能显示，地址本身仍然缓存下来 */
function probe(url) {
  return new Promise((resolve) => {
    const img = new Image()
    const done = (ok) => {
      img.onload = img.onerror = null
      resolve(ok)
    }
    img.onload = () => done(img.naturalWidth > 0)
    img.onerror = () => done(false)
    img.src = url
    setTimeout(() => done(false), 6000)
  })
}

/** 同一个图标并发只取一次 */
const inflight = new Map()

/** 命中缓存时同步返回，用作首屏初值，避免闪一下文字徽标 */
function fresh(hit) {
  if (!hit) return false
  const ttl = hit.data || hit.url ? TTL : FAIL_TTL
  return Date.now() - hit.ts < ttl
}

export function peekIcon(item) {
  const hit = cache[keyOf(item)]
  return fresh(hit) ? hit.data || hit.url || '' : ''
}

export async function loadIcon(item) {
  const key = keyOf(item)
  if (!key) return ''

  const hit = cache[key]
  if (fresh(hit)) return hit.data || hit.url || ''

  if (inflight.has(key)) return inflight.get(key)

  const task = (async () => {
    const api = apiOf(item)
    if (api) {
      const data = await viaProxy(api)
      if (data) {
        cache[key] = { data, ts: Date.now() }
        persist()
        return data
      }
    }
    for (const url of fallbackUrls(item)) {
      if (await probe(url)) {
        cache[key] = { url, ts: Date.now() }
        persist()
        return url
      }
    }
    cache[key] = { ts: Date.now() }   // 记下"取不到"，下次不再重复请求
    persist()
    return ''
  })()

  inflight.set(key, task)
  try {
    return await task
  } finally {
    inflight.delete(key)
  }
}

export function clearIconCache() {
  cache = {}
  try { localStorage.removeItem(KEY) } catch {}
}
