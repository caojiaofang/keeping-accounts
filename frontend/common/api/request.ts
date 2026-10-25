import { appConfig } from '@/common/config'

export const API_BASE_URL = appConfig.apiBaseUrl

export class ApiError extends Error {
  constructor(message: string, public readonly status = 0) { super(message) }
}

let unauthorizedHandler: ((token: string) => void) | undefined
export function onUnauthorized(handler: (token: string) => void): void { unauthorizedHandler = handler }
export function notifyUnauthorized(token: string): void { unauthorizedHandler?.(token) }

export function apiRequest<T>(path: string, options: {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  data?: Record<string, unknown>
  token?: string
  timeout?: number
} = {}): Promise<T> {
  return new Promise((resolve, reject) => {
    uni.request({
      url: `${API_BASE_URL}${path}`,
      method: options.method || 'GET',
      data: options.data,
      timeout: options.timeout || appConfig.requestTimeout,
      header: options.token ? { Authorization: `Bearer ${options.token}` } : {},
      success(response) {
        if (response.statusCode >= 200 && response.statusCode < 300) {
          resolve(response.data as T)
          return
        }
        if (response.statusCode === 401 && options.token) unauthorizedHandler?.(options.token)
        const body = response.data as { detail?: unknown } | null
        const message = body && typeof body.detail === 'string' ? body.detail : '服务暂时不可用，请稍后重试'
        reject(new ApiError(message, response.statusCode))
      },
      fail() { reject(new ApiError('暂时连接不到服务，请检查网络后重试')) },
    })
  })
}
