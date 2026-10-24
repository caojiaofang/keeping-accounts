import type { Book, BookInvitation, BookMember, Entry } from './types'
import { localDate } from './format'

// 本地演示身份；真实用户将由后端微信登录态确定。
export const DEMO_USER_ID = 'demo-me'
export const FAMILY_INVITATION = 'family-example'
export const familyPartner = { userId: 'demo-partner', name: '家人（示例）', color: '#f1e3cf' }
export const demoSelf = { userId: DEMO_USER_ID, name: '我', color: '#dcece1' }

export function activeMembers(book?: Book): BookMember[] {
  return book?.members?.filter(member => member.status === 'active') || []
}

export function canWriteBook(book?: Book, userId = DEMO_USER_ID): boolean {
  return !!book && (book.kind !== 'shared' || activeMembers(book).some(member => member.userId === userId))
}

export function canManageBook(book?: Book, userId = DEMO_USER_ID): boolean {
  return !!book && (book.kind !== 'shared' || activeMembers(book).some(member => member.userId === userId && member.role === 'owner'))
}

export function canEditEntry(book: Book | undefined, entry: Entry, userId = DEMO_USER_ID): boolean {
  return canWriteBook(book, userId) && (book?.kind !== 'shared' || canManageBook(book, userId) || entry.createdBy === userId)
}

export function entryAuthor(book: Book | undefined, entry: Entry): string {
  if (book?.kind !== 'shared') return ''
  const member = book.members?.find(item => item.userId === entry.createdBy)
  if (!member) return '历史记账人'
  return `${member.name}${member.status === 'removed' ? '（已移出）' : ''}`
}

export function newSharedBook(name: string, id: string): Book {
  return { id, name, description: '和在意的人，一起记录生活', icon: 'home', color: '#e5eddf', budget: 800000, kind: 'shared', members: [{ ...demoSelf, role: 'owner', status: 'active' }] }
}

export function validInvitation(invitation: BookInvitation, book?: Book, now = Date.now()): boolean {
  return invitation.bookId === book?.id && book.kind === 'shared' && !invitation.revoked && invitation.expiresAt > now
}

export function memberTotals(entries: readonly Entry[], bookId: string, month: string, userId?: string) {
  const filtered = entries.filter(entry => entry.bookId === bookId && entry.date.startsWith(month) && (!userId || entry.createdBy === userId))
  const expense = filtered.filter(entry => entry.type === 'expense').reduce((sum, entry) => sum + entry.amount, 0)
  const income = filtered.filter(entry => entry.type === 'income').reduce((sum, entry) => sum + entry.amount, 0)
  return { count: filtered.length, expense, income, balance: income - expense }
}

// 仅在用户主动体验时创建独立示例，不改动已有的「小家账本」。
export function familyExample(asInvitee: boolean, now = new Date()) {
  const book = newSharedBook(asInvitee ? '受邀家庭账本（示例）' : '家庭共同账本（示例）', asInvitee ? 'shared-joined-example' : 'shared-family-example')
  book.description = '示例数据，仅用于体验共同记账'
  book.members = [
    { ...demoSelf, role: asInvitee ? 'member' : 'owner', status: 'active' },
    { ...familyPartner, role: asInvitee ? 'owner' : 'member', status: 'active' },
  ]
  const records = [
    { categoryId: 'daily', amount: 12860, note: '超市采购，一周的日常', createdBy: familyPartner.userId, type: 'expense' as const },
    { categoryId: 'food', amount: 8600, note: '两个人的晚餐', createdBy: DEMO_USER_ID, type: 'expense' as const },
    { categoryId: 'salary', amount: 600000, note: '本月家庭收入', createdBy: familyPartner.userId, type: 'income' as const },
  ]
  const entries: Entry[] = records.map((record, index) => ({ ...record, id: `${book.id}-${index}`, bookId: book.id, date: localDate(now), time: ['10:20', '18:30', '09:00'][index], account: '微信', createdAt: now.getTime() + index }))
  return { book, entries }
}
