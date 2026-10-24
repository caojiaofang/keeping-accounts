import { API_BASE_URL, apiRequest } from '@/common/api/request'
import type { InvitationPreview } from '@/common/types'

export function isCloudInvitation(token: string): boolean {
  return /^s1_[0-9a-f]{32}_[0-9a-f]{64}$/.test(token)
}

export function invitationPath(token: string): string {
  return `/pages/book-invite/index?token=${encodeURIComponent(token)}`
}

const PENDING_KEY = `moji-pending-invite-v1:${API_BASE_URL}`
let pendingToken = ''

export function rememberInvitation(token?: string): void {
  if (!token || !isCloudInvitation(token)) return
  pendingToken = token
  try { uni.setStorageSync(PENDING_KEY, token) } catch { /* 内存仍可保留本次登录返回地址。 */ }
}

export function pendingInvitationPath(): string {
  try { pendingToken = String(uni.getStorageSync(PENDING_KEY) || pendingToken) } catch { /* 使用内存中的返回地址。 */ }
  return isCloudInvitation(pendingToken) ? invitationPath(pendingToken) : ''
}

export function forgetInvitation(): void {
  pendingToken = ''
  try { uni.removeStorageSync(PENDING_KEY) } catch { /* 过期凭证仍由服务端拒绝。 */ }
}

export function previewInvitation(token: string): Promise<InvitationPreview> {
  return apiRequest('/api/v1/book-invitations/preview', { method: 'POST', data: { token } })
}
