/**
 * API 客户端 — 封装与后端通信的底层方法。
 *
 * 设计决策（对齐 T2.17 app.js 的 csrfToken/apiWrite/apiGet）：
 * 1. CSRF 双提交：cookie 由服务端 Set-Cookie 设置，token 从 /api/csrf-token 响应体获取
 *    （不从 document.cookie 读取，因为 cookie 是 HttpOnly 的）
 * 2. 写请求（POST/PUT/PATCH/DELETE）带 X-CSRFToken 头 + same-origin 凭据
 * 3. 读请求（GET）带 same-origin 凭据
 * 4. T3.2 会用 openapi-typescript 自动生成 TS 类型，此处只做骨架
 */

const API_BASE = ''  // Vite 代理已处理 /api 前缀

let _csrfToken: string | null = null

/** 获取 CSRF token（首次调用时从 /api/csrf-token 获取并缓存） */
export async function getCsrfToken(): Promise<string> {
  if (_csrfToken) return _csrfToken
  const res = await fetch(`${API_BASE}/api/csrf-token`, { credentials: 'same-origin' })
  if (!res.ok) throw new Error(`获取 CSRF token 失败: ${res.status}`)
  const data = await res.json()
  _csrfToken = data.csrf_token
  return _csrfToken!
}

/** 刷新 CSRF token（登录后、403 后调用） */
export async function refreshCsrfToken(): Promise<string> {
  _csrfToken = null
  return getCsrfToken()
}

/** 通用请求方法 */
async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }

  // 写请求需要 CSRF token
  if (method !== 'GET' && method !== 'HEAD') {
    const token = await getCsrfToken()
    headers['X-CSRFToken'] = token
  }

  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    credentials: 'same-origin',
    body: body ? JSON.stringify(body) : undefined,
  })

  // CSRF 失败时刷新 token 并重试一次
  if (res.status === 403 && method !== 'GET') {
    const token = await refreshCsrfToken()
    headers['X-CSRFToken'] = token
    const retry = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      credentials: 'same-origin',
      body: body ? JSON.stringify(body) : undefined,
    })
    if (!retry.ok) throw new ApiError(retry.status, await retry.text())
    return retry.json()
  }

  if (!res.ok) throw new ApiError(res.status, await res.text())
  return res.json()
}

/** API 错误 */
export class ApiError extends Error {
  status: number
  body: string
  constructor(status: number, body: string) {
    // 后端统一错误结构 {error, detail, status}：优先透出可读的 detail，
    // 避免把整坨 JSON 甩进 Toast
    let friendly = body
    try {
      const parsed = JSON.parse(body) as { detail?: unknown; error?: unknown }
      if (typeof parsed.detail === 'string') friendly = parsed.detail
      else if (parsed.detail) friendly = JSON.stringify(parsed.detail)
    } catch {
      /* 非 JSON 响应体，原样保留 */
    }
    super(`API ${status}: ${friendly}`)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

/** GET 请求 */
export const apiGet = <T>(path: string) => request<T>('GET', path)

/** POST 请求 */
export const apiPost = <T>(path: string, body?: unknown) => request<T>('POST', path, body)

/** PUT 请求 */
export const apiPut = <T>(path: string, body?: unknown) => request<T>('PUT', path, body)

/** DELETE 请求 */
export const apiDelete = <T>(path: string) => request<T>('DELETE', path)