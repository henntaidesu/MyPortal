import { getOrigin } from './utils'

/**
 * 图标由后端代取、并缓存在服务器的 backend/icons 目录里（见 app/iconcache.py），
 * 所以这里只负责拼出该请求哪个地址，不再自己攒缓存。
 *
 * 重复访问由两层挡着：浏览器按 /api/icon 响应上的 cache-control 缓存一天，
 * 过期之后后端也是从磁盘直接给，不会再去外网。抓不到的站点后端同样记着（.miss 文件），
 * 不会每开一次门户就白跑一趟外网。
 */

/* 这份缓存原来在 localStorage 里，搬到后端了。把浏览器里那两份旧的清掉，腾出配额 */
for (const stale of ['portal-nav-icons', 'home-nav-icons']) {
  try { localStorage.removeItem(stale) } catch { /* 不成也无所谓 */ }
}

/** 卡片该显示的图片地址；返回空串表示没有图，让调用方退回文字徽标 */
export function iconUrl(item) {
  const custom = (item.icon || '').trim()
  if (custom.startsWith('data:')) return custom           // 直接贴的 base64，不用过后端
  if (/^(https?:\/\/|\/)/i.test(custom)) {
    return '/api/icon?url=' + encodeURIComponent(custom)  // 指定了图标地址，代取这一张
  }
  const origin = getOrigin(item.url)
  // 没指定就把整套 favicon 解析交给后端：<link rel=icon> 解析不到再退 /favicon.ico
  return origin ? '/api/icon?site=' + encodeURIComponent(origin) : ''
}
