import { invitationPath } from './invitations'

/** 普通分享只携带小程序入口，使用固定封面，避免微信截取当前账本画面。 */
export function appShare() {
  return {
    title: '墨记，把日子一笔一笔记好',
    path: '/pages/ledger/index',
    imageUrl: '/static/share-cover.png',
  }
}

interface BookShareContext {
  from: string
  loggedIn: boolean
  owner: boolean
  inviting: boolean
  valid: boolean
  token: string
  bookName: string
}

export function bookShare(context: BookShareContext) {
  if (context.from === 'button' && context.loggedIn && context.owner && context.inviting && context.valid && context.token) {
    return { ...appShare(), title: `邀请你加入「${context.bookName || '共同账本'}」`, path: invitationPath(context.token) }
  }
  return appShare()
}
