export const uid = () => Math.random().toString(36).slice(2, 9) + Date.now().toString(36).slice(-3)

/** 没写协议就补 http://，方便直接粘贴 192.168.x.x:8080 */
export function normalizeUrl(url) {
  const u = (url || '').trim()
  if (!u) return ''
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(u) || /^\/\//.test(u)) return u
  return 'http://' + u
}

export function getHost(url) {
  try {
    return new URL(normalizeUrl(url)).host
  } catch {
    return url || ''
  }
}

export function getOrigin(url) {
  try {
    return new URL(normalizeUrl(url)).origin
  } catch {
    return ''
  }
}

export function initials(name) {
  const n = (name || '?').trim()
  if (!n) return '?'
  if (/[一-龥]/.test(n[0])) return n[0]
  return n.slice(0, 2).toUpperCase()
}

const PALETTE = ['#4a6cf7', '#e5484d', '#29a745', '#e8a33d', '#8b5cf6', '#0ea5e9', '#ec4899', '#14b8a6']

/** 由名称稳定生成配色，同一个系统颜色不会变 */
export function colorOf(str) {
  let h = 0
  for (const ch of str || '') h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return PALETTE[h % PALETTE.length]
}

export function download(filename, text) {
  const blob = new Blob([text], { type: 'application/json;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename
  a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 1000)
}
