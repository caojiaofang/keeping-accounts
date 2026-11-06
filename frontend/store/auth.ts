import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { API_BASE_URL, ApiError, apiRequest, onUnauthorized } from '@/common/api/request'
import { isAuthSession, isAuthUser, requestWeChatCode } from '@/common/services/auth'
import type { AuthSession, AuthUser, LoginResult } from '@/common/services/auth'
import { useLedgerStore } from '@/store/ledger'

// 按后端地址隔离凭证，开发服务与其他环境不会共用 token。
export const AUTH_STORAGE_KEY = `moji-auth-v1:${API_BASE_URL}`

export const useAuthStore = defineStore('auth', () => {
  const user = ref<AuthUser | null>(null)
  const ready = ref(false)
  const restoring = ref(false)
  const loggingIn = ref(false)
  const loggingOut = ref(false)
  const savingNickname = ref(false)
  const error = ref('')
  const isLoggedIn = computed(() => user.value !== null)
  const busy = computed(() => restoring.value || loggingIn.value || loggingOut.value || savingNickname.value)
  let session: AuthSession | null = null
  let revision = 0
  let restoreTask: Promise<void> | undefined
  let loginTask: Promise<void> | undefined
  let lastVerified = 0

  function readSaved(): AuthSession | null {
    try {
      const saved: unknown = uni.getStorageSync(AUTH_STORAGE_KEY)
      if (isAuthSession(saved)) return saved
      if (saved) uni.removeStorageSync(AUTH_STORAGE_KEY)
    } catch { /* 缓存不可用时保留登录入口，不信任残缺的身份信息。 */ }
    return null
  }

  function clearLocal(message = ''): void {
    revision++
    session = null
    user.value = null
    restoring.value = false
    error.value = message
    try { uni.removeStorageSync(AUTH_STORAGE_KEY) } catch { /* 服务端失效校验仍会拒绝旧凭证。 */ }
    useLedgerStore().switchAccount(null)
  }

  function cacheNickname(current: AuthUser): void {
    if (!current.nickname) return
    // 保留旧版成员称呼入口的本地缓存；账号昵称始终以后端为准。
    try { uni.setStorageSync(useLedgerStore().preferenceKey('nickname'), current.nickname) } catch { /* 缓存失败不影响云端昵称。 */ }
  }

  async function activate(value: AuthSession): Promise<void> {
    try { uni.setStorageSync(AUTH_STORAGE_KEY, value) }
    catch { throw new Error('本地空间不足，登录状态未保存，请清理空间后重试') }
    useLedgerStore().switchAccount(value.user.id, value.accessToken)
    session = value
    user.value = value.user
    cacheNickname(value.user)
    lastVerified = Date.now()
    error.value = ''
    await useLedgerStore().refreshCloud()
  }

  function returnToBrowsing(message: string): void {
    clearLocal(message)
    const pages = getCurrentPages()
    const route = pages[pages.length - 1]?.route
    // 关闭旧账号的编辑页面，回到可直接体验的首页；登录页仍可由用户自主重试。
    if (route !== 'pages/login/index' && route !== 'pages/ledger/index') {
      uni.reLaunch({ url: '/pages/ledger/index' })
    }
  }

  onUnauthorized(token => {
    if (session?.accessToken !== token) return
    returnToBrowsing('登录已过期，当前为本地体验，可在“我的”重新登录')
  })

  async function restoreImpl(): Promise<void> {
    const saved = readSaved()
    if (!saved) {
      clearLocal()
      ready.value = true
      return
    }
    const version = revision
    session = saved
    restoring.value = true
    try {
      const current = await apiRequest<AuthUser>('/api/v1/auth/me', { token: saved.accessToken })
      if (version !== revision) return
      if (!isAuthUser(current) || current.id !== saved.user.id) {
        returnToBrowsing('登录信息校验失败，当前为本地体验，可在“我的”重新登录')
        return
      }
      await activate({ ...saved, user: current })
    } catch (cause) {
      if (version === revision) error.value = cause instanceof Error ? cause.message : '登录验证失败，请重试'
    } finally {
      if (version === revision) restoring.value = false
      ready.value = true
    }
  }

  function restore(): Promise<void> {
    if (restoreTask) return restoreTask
    if (savingNickname.value) return Promise.resolve()
    restoreTask = restoreImpl().finally(() => { restoreTask = undefined })
    return restoreTask
  }

  function bootstrap(): Promise<void> { return ready.value ? Promise.resolve() : restore() }

  function login(): Promise<void> {
    if (loginTask) return loginTask
    if (restoring.value || loggingOut.value || savingNickname.value) return Promise.resolve()
    loginTask = (async () => {
      loggingIn.value = true
      error.value = ''
      const version = ++revision
      let issuedToken = ''
      try {
        const code = await requestWeChatCode()
        const result = await apiRequest<LoginResult>('/api/v1/auth/wechat/login', { method: 'POST', data: { code } })
        issuedToken = typeof result.access_token === 'string' ? result.access_token : ''
        if (!/^[A-Za-z0-9_-]{43}$/.test(issuedToken) || result.token_type !== 'bearer'
          || !isAuthUser(result.user) || !Number.isFinite(result.expires_in) || result.expires_in <= 0) {
          throw new Error('登录响应不完整，请重新尝试')
        }
        if (version !== revision) throw new Error('登录状态已变化，请重新尝试')
        const oldToken = session?.accessToken
        await activate({ accessToken: issuedToken, expiresAt: Date.now() + result.expires_in * 1000, user: result.user })
        if (oldToken && oldToken !== issuedToken) {
          void apiRequest('/api/v1/auth/logout', { method: 'POST', token: oldToken }).catch(() => {})
        }
      } catch (cause) {
        if (issuedToken) {
          void apiRequest('/api/v1/auth/logout', { method: 'POST', token: issuedToken }).catch(() => {})
        }
        error.value = cause instanceof Error ? cause.message : '登录失败，请重新尝试'
      } finally {
        loggingIn.value = false
        ready.value = true
      }
    })().finally(() => { loginTask = undefined })
    return loginTask
  }

  async function logout(): Promise<void> {
    if (busy.value) return
    loggingOut.value = true
    const token = session?.accessToken
    try {
      if (token) await apiRequest('/api/v1/auth/logout', { method: 'POST', token })
      clearLocal()
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) clearLocal()
      else throw cause
    } finally { loggingOut.value = false }
  }

  async function updateNickname(value: string): Promise<boolean> {
    const nickname = value.trim()
    if (!nickname || Array.from(nickname).length > 12) throw new Error('昵称需为 1 至 12 个字')
    if (busy.value) return false
    if (!session || !user.value) throw new Error('请先登录后再修改昵称')
    const currentSession = session
    const version = revision
    savingNickname.value = true
    try {
      const current = await apiRequest<AuthUser>('/api/v1/auth/me', {
        method: 'PUT', token: currentSession.accessToken, data: { nickname },
      })
      if (version !== revision || session !== currentSession) return false
      if (!isAuthUser(current) || current.id !== currentSession.user.id || current.nickname !== nickname) {
        throw new Error('昵称保存结果异常，请刷新后重试')
      }
      session = { ...currentSession, user: current }
      user.value = current
      // 云端已经提交成功，本地空间不足时仍可在下次登录校验中恢复昵称。
      try { uni.setStorageSync(AUTH_STORAGE_KEY, session) } catch { /* 保留原登录凭证，下次从云端读取。 */ }
      cacheNickname(current)
      return true
    } finally { savingNickname.value = false }
  }

  function enterGuest(): void {
    if (busy.value) return
    const token = session?.accessToken
    clearLocal()
    ready.value = true
    if (token) void apiRequest('/api/v1/auth/logout', { method: 'POST', token }).catch(() => {})
  }

  async function verifyIfNeeded(): Promise<void> {
    if (!ready.value || !session || busy.value) return
    if (session.expiresAt <= Date.now()) {
      returnToBrowsing('登录已过期，当前为本地体验，可在“我的”重新登录')
    } else if (Date.now() - lastVerified > 60000) await restore()
    else await useLedgerStore().refreshCloud()
  }

  return { user, ready, restoring, loggingIn, loggingOut, savingNickname, isLoggedIn, busy, error, bootstrap, restore, login, logout, enterGuest, updateNickname, verifyIfNeeded }
})
