/** 跟后端说话的唯一出口。后端地址走同源，部署时不用配 CORS。 */

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.status = status
  }
}

/** 后端没起来 / 网线断了，跟「密码错」要分开提示 */
export class OfflineError extends Error {
  constructor() {
    super('连不上认证服务，请确认后端已启动')
  }
}

/* 会话过期时谁来管。由 auth.js 注册进来——反过来让 api.js import auth.js 会成环 */
let onUnauthorized = () => {}
export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn
}

export async function api(path, { method = 'GET', body } = {}) {
  let res
  try {
    res = await fetch(path, {
      method,
      credentials: 'same-origin',
      headers: body ? { 'content-type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined
    })
  } catch {
    throw new OfflineError()
  }

  if (res.status === 204) return null

  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    // Cookie 过期或被换掉了。/api/me 不会走到这里（它没登录也返回 200），
    // 所以 401 一定是「本来登着、现在不算数了」，直接把人退回登录页
    if (res.status === 401) onUnauthorized()
    throw new ApiError(data.detail || `请求失败（${res.status}）`, res.status)
  }
  return data
}
