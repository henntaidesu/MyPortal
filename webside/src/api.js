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
    throw new ApiError(data.detail || `请求失败（${res.status}）`, res.status)
  }
  return data
}
