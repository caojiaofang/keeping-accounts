import { reactive } from 'vue'
import type { Identity } from './types'

export const auth = reactive<{ user: Identity | null; checked: boolean }>({ user: null, checked: false })

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message) }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const userAtStart = auth.user
  let response: Response
  try {
    response = await fetch(`/prod-api${path}`, {
      ...options, credentials: 'same-origin', cache: 'no-store',
      headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
      signal: options.signal ?? AbortSignal.timeout(20000),
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError('连接失败或请求超时，请检查网络后重试', 0)
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    if (response.status === 401 && path !== '/auth/login' && auth.user === userAtStart) {
      auth.user = null
      window.dispatchEvent(new Event('moji-session-expired'))
    }
    throw new ApiError(typeof body.detail === 'string' ? body.detail : '服务暂时不可用，请稍后重试', response.status)
  }
  return response.status === 204 ? undefined as T : response.json()
}

export async function restoreSession() {
  try { auth.user = await api<Identity>('/auth/me') }
  catch (error) { if (!(error instanceof ApiError) || error.status !== 401) throw error }
  auth.checked = true
}

export function query(params: Record<string, string | number | undefined>) {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => { if (value !== undefined && value !== '') search.set(key, String(value)) })
  return `?${search.toString()}`
}
