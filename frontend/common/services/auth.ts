export interface AuthUser {
  id: string
  openid: string
  created_at: string
  last_login_at: string
}

export interface AuthSession {
  accessToken: string
  expiresAt: number
  user: AuthUser
}

export interface LoginResult {
  access_token: string
  token_type: 'bearer'
  expires_in: number
  is_new_user: boolean
  user: AuthUser
}

export function isAuthUser(value: unknown): value is AuthUser {
  if (!value || typeof value !== 'object') return false
  const user = value as AuthUser
  return typeof user.id === 'string' && /^[\da-f]{8}(-[\da-f]{4}){3}-[\da-f]{12}$/i.test(user.id)
    && typeof user.openid === 'string' && !!user.openid.trim()
    && typeof user.created_at === 'string' && Number.isFinite(Date.parse(user.created_at))
    && typeof user.last_login_at === 'string' && Number.isFinite(Date.parse(user.last_login_at))
}

export function isAuthSession(value: unknown): value is AuthSession {
  if (!value || typeof value !== 'object') return false
  const session = value as AuthSession
  return typeof session.accessToken === 'string' && /^[A-Za-z0-9_-]{43}$/.test(session.accessToken)
    && Number.isFinite(session.expiresAt) && session.expiresAt > Date.now()
    && isAuthUser(session.user)
}

export function safeLoginRedirect(value?: string): string {
  const allowed = ['/pages/ledger/index', '/pages/profile/index', '/pages/books/index', '/pages/statistics/index', '/pages/shared-books/index', '/pages/assistant/index']
  try {
    const target = value ? decodeURIComponent(value) : ''
    if (/^\/pages\/book-invite\/index\?token=s1_[0-9a-f]{32}_[0-9a-f]{64}$/.test(target)) return target
    return allowed.includes(target) ? target : '/pages/ledger/index'
  } catch { return '/pages/ledger/index' }
}

export function requestWeChatCode(): Promise<string> {
  // #ifdef MP-WEIXIN
  return new Promise((resolve, reject) => {
    uni.login({
      provider: 'weixin',
      success(result) {
        if (result.code) resolve(result.code)
        else reject(new Error('微信未返回登录凭证，请重新尝试'))
      },
      fail() { reject(new Error('微信登录未完成，请重新尝试')) },
    })
  })
  // #endif
  // #ifndef MP-WEIXIN
  return Promise.reject(new Error('请在微信小程序中登录，也可以先体验本地记账'))
  // #endif
}
