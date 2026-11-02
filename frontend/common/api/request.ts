import { appConfig } from '@/common/config'

export const API_BASE_URL = appConfig.apiBaseUrl

export type NetworkFailure = 'domain' | 'tls' | 'timeout' | 'network'

export class ApiError extends Error {
  constructor(message: string, public readonly status = 0, public readonly networkFailure?: NetworkFailure) { super(message) }
}

function networkError(errMsg: string): ApiError {
  if (/url not in domain list|domain.*not.*(?:allowed|authorized)|不在.*合法域名/i.test(errMsg)) {
    return new ApiError('服务连接配置异常，请稍后重试', 0, 'domain')
  }
  if (/ssl|tls|certificate|cert[_ :]|证书/i.test(errMsg)) {
    return new ApiError('暂时无法建立安全连接，请稍后重试', 0, 'tls')
  }
  if (/timeout|timed out|超时/i.test(errMsg)) {
    return new ApiError('服务连接超时，请稍后重试', 0, 'timeout')
  }
  return new ApiError('暂时连接不到服务，请检查网络后重试', 0, 'network')
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
      fail(cause) {
        const error = networkError(cause.errMsg || '')
        // 仅记录环境、接口路径和失败类别，不记录 code、token、请求体或完整原始错误。
        console.warn('墨记接口连接失败', {
          envVersion: appConfig.envVersion,
          apiBaseUrl: API_BASE_URL,
          path: path.split('?')[0],
          failure: error.networkFailure,
        })
        reject(error)
      },
    })
  })
}
