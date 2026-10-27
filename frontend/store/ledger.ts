import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { categories } from '@/common/data/categories'
import { createDemoEntries, initialBooks } from '@/common/mock/ledger'
import type { Book, BookActivity, BookInvitation, Entry } from '@/common/types'
import { localDate, toast } from '@/common/utils/format'
import { canEditEntry, canManageBook, canWriteBook, DEMO_USER_ID, familyExample, newSharedBook, validInvitation } from '@/common/services/shared-books'
import { API_BASE_URL, apiRequest } from '@/common/api/request'
import { chatStream } from '@/common/api/chat-stream'
import type { ChatStreamEvent } from '@/common/types/chat'
import { newId } from '@/common/utils/id'
import type { AssistantDraft } from '@/common/mock/assistant'
import type { RecordInput, RecordResult } from '@/common/types/record'

const STORAGE_KEY = 'moji-demo-v1'

interface StoredState {
  entries: Entry[]
  books: Book[]
  activeBookId: string
  invitations?: BookInvitation[]
}

export const useLedgerStore = defineStore('ledger', () => {
  const entries = ref<Entry[]>([])
  const books = ref<Book[]>(initialBooks.map(book => ({ ...book })))
  const activeBookId = ref('daily')
  const selectedMonth = ref(localDate().slice(0, 7))
  const storageAvailable = ref(true)
  const invitations = ref<BookInvitation[]>([])
  const accountId = ref<string | null>(null)
  const syncing = ref(false)
  const writing = ref(false)
  const cloudReady = ref(false)
  const syncError = ref('')
  const legacyEntries = ref<Entry[]>([])
  let cloudToken = ''
  let revision = 0
  let syncTask: Promise<boolean> | undefined
  const pendingCreates = new Map<string, string>()
  let initialized = false

  function preferenceKey(name: string): string {
    if (name === 'ledger' && accountId.value) return `moji-cloud-v1:${API_BASE_URL}:${accountId.value}:ledger`
    if (accountId.value) return `moji-user-v1:${accountId.value}:${name}`
    return name === 'ledger' ? STORAGE_KEY : `moji-${name}`
  }

  function switchAccount(userId: string | null, token = ''): void {
    cloudToken = token
    if (initialized && accountId.value === userId) return
    revision++
    syncTask = undefined
    syncing.value = false
    writing.value = false
    cloudReady.value = false
    syncError.value = ''
    legacyEntries.value = []
    pendingCreates.clear()
    accountId.value = userId
    entries.value = []
    books.value = (userId ? initialBooks.slice(0, 1) : initialBooks).map(book => ({ ...book }))
    invitations.value = []
    activeBookId.value = 'daily'
    selectedMonth.value = localDate().slice(0, 7)
    initialized = false
    hydrate()
    if (userId) {
      try {
        const legacy = uni.getStorageSync(`moji-user-v1:${userId}:ledger`) as StoredState | undefined
        if (Array.isArray(legacy?.entries)) legacyEntries.value = legacy.entries
      } catch { /* 原有本机数据仍留在原存储区。 */ }
    }
  }

  const activeBook = computed(() => books.value.find(book => book.id === activeBookId.value) || books.value[0] || initialBooks[0])
  const bookEntries = computed(() => entries.value.filter(entry => entry.bookId === activeBookId.value))
  const monthEntries = computed(() => bookEntries.value.filter(entry => entry.date.startsWith(selectedMonth.value)))
  const expense = computed(() => monthEntries.value.filter(entry => entry.type === 'expense').reduce((sum, entry) => sum + entry.amount, 0))
  const income = computed(() => monthEntries.value.filter(entry => entry.type === 'income').reduce((sum, entry) => sum + entry.amount, 0))
  const balance = computed(() => income.value - expense.value)

  function persist(): void {
    try {
      uni.setStorageSync(preferenceKey('ledger'), { entries: entries.value, books: books.value, activeBookId: activeBookId.value, invitations: invitations.value })
      storageAvailable.value = true
    } catch {
      storageAvailable.value = false
      uni.showToast({ title: accountId.value ? '本机缓存空间不足，请保持联网使用' : '本地空间不足，本次更改仅在当前运行中保留', icon: 'none' })
    }
  }

  function hydrate(): void {
    if (initialized) return
    initialized = true
    try {
      const saved = uni.getStorageSync(preferenceKey('ledger')) as StoredState | undefined
      if (saved && Array.isArray(saved.entries) && Array.isArray(saved.books) && saved.books.length) {
        entries.value = saved.entries
        books.value = saved.books
        invitations.value = Array.isArray(saved.invitations) ? saved.invitations : []
        activeBookId.value = saved.books.some(book => book.id === saved.activeBookId) ? saved.activeBookId : saved.books[0].id
        return
      }
    } catch {
      storageAvailable.value = false
    }
    entries.value = accountId.value ? [] : createDemoEntries()
    persist()
  }

  function refreshCloud(): Promise<boolean> {
    if (!accountId.value || !cloudToken || writing.value) return Promise.resolve(false)
    if (syncTask) return syncTask
    const version = revision
    const token = cloudToken
    syncing.value = true
    syncError.value = ''
    let task!: Promise<boolean>
    task = (async () => {
      try {
        const state = await apiRequest<StoredState>('/api/v1/ledger', { token })
        if (version !== revision) return false
        if (!Array.isArray(state.books) || !state.books.length || !Array.isArray(state.entries)) throw new Error('账本响应不完整，请重试')
        books.value = state.books
        entries.value = state.entries
        invitations.value = invitations.value.filter(invite => books.value.some(book => book.id === invite.bookId && canManageBook(book)))
        if (!books.value.some(book => book.id === activeBookId.value)) activeBookId.value = books.value[0].id
        cloudReady.value = true
        persist()
        return true
      } catch (cause) {
        if (version === revision) syncError.value = cause instanceof Error ? cause.message : '云端账本读取失败，请重试'
        return false
      } finally {
        if (syncTask === task) { syncing.value = false; syncTask = undefined }
      }
    })()
    syncTask = task
    return task
  }

  async function cloudWrite<T>(action: (token: string) => Promise<T>, apply: (result: T) => void): Promise<boolean> {
    if (writing.value) { toast('正在保存，请稍候'); return false }
    if (!cloudReady.value || !cloudToken) { toast('请先刷新并连接云端账本'); return false }
    const version = ++revision
    syncTask = undefined
    syncing.value = false
    const token = cloudToken
    writing.value = true
    try {
      const result = await action(token)
      if (version !== revision) return false
      apply(result)
      syncError.value = ''
      persist()
      return true
    } catch (cause) {
      if (version === revision) {
        syncError.value = cause instanceof Error ? cause.message : '保存失败，请重试'
        toast(syncError.value)
      }
      return false
    } finally { if (version === revision) writing.value = false }
  }

  async function recognizeDrafts(text: string): Promise<{ drafts: AssistantDraft[]; clarification?: string }> {
    if (!accountId.value || !cloudToken || !cloudReady.value) throw new Error('请先登录并连接云端账本')
    const user = accountId.value
    const result = await apiRequest<{ drafts: AssistantDraft[]; clarification?: string }>('/api/v1/assistant/parse', {
      method: 'POST', token: cloudToken, timeout: 100000, data: { text, bookId: activeBookId.value },
    })
    if (user !== accountId.value) throw new Error('账号已变化，请重新识别')
    return result
  }

  async function recordDrafts(input: RecordInput): Promise<RecordResult> {
    if (!accountId.value || !cloudToken || !cloudReady.value) throw new Error('请先登录并连接云端账本')
    const user = accountId.value
    const book = activeBookId.value
    const token = cloudToken
    const result = await apiRequest<RecordResult>('/api/v1/assistant/record', {
      method: 'POST', token, timeout: 100000, data: { ...input, bookId: book },
    })
    if (user !== accountId.value || book !== activeBookId.value || token !== cloudToken) throw new Error('账号或账本已变化，请重新发送')
    return result
  }

  async function chatRequest<T>(path: string, options: { method?: 'GET' | 'POST'; data?: Record<string, unknown> } = {}): Promise<T> {
    if (!accountId.value || !cloudToken || !cloudReady.value) throw new Error('请先登录并连接云端账本')
    const user = accountId.value
    const result = await apiRequest<T>(`/api/v1/assistant${path}`, { ...options, token: cloudToken, timeout: 100000 })
    if (user !== accountId.value) throw new Error('账号已变化，请重新打开对话')
    return result
  }

  async function authorizedRead<T>(path: string): Promise<T> {
    if (!accountId.value || !cloudToken) throw new Error('请先微信登录')
    const user = accountId.value
    const result = await apiRequest<T>(path, { token: cloudToken })
    if (user !== accountId.value) throw new Error('账号已变化，请重新读取')
    return result
  }

  function streamChat(path: string, data: Record<string, unknown>, receive: (event: ChatStreamEvent) => void) {
    if (!accountId.value || !cloudToken || !cloudReady.value) throw new Error('请先登录并连接云端账本')
    return chatStream(`/api/v1/assistant${path}`, cloudToken, data, receive)
  }

  function saveEntry(entry: Omit<Entry, 'id' | 'createdAt' | 'createdBy' | 'updatedBy' | 'updatedAt'>, id?: string, requestId?: string): boolean | Promise<boolean> {
    const targetBook = books.value.find(book => book.id === entry.bookId)
    if (!canWriteBook(targetBook)) { toast('你还不是这个账本的成员'); return false }
    if (accountId.value) {
      const original = id ? entries.value.find(item => item.id === id) : undefined
      if (id && !original) { toast('这笔账单不存在'); return false }
      const key = JSON.stringify(entry)
      const createId = requestId || pendingCreates.get(key) || newId()
      if (!id) pendingCreates.set(key, createId)
      const { version: ignoredVersion, ...input } = entry
      return cloudWrite(token => apiRequest<Entry>(id ? `/api/v1/entries/${id}` : '/api/v1/entries', {
        method: id ? 'PUT' : 'POST', token,
        data: id ? { ...input, version: ignoredVersion ?? original!.version } : { ...input, id: createId },
      }), saved => {
        entries.value = [...entries.value.filter(item => item.id !== saved.id), saved]
        selectedMonth.value = saved.date.slice(0, 7)
        activeBookId.value = saved.bookId
        pendingCreates.delete(key)
      })
    }
    if (id) {
      const index = entries.value.findIndex(item => item.id === id)
      if (index < 0) { toast('这笔账单不存在'); return false }
      if (!canEditEntry(books.value.find(book => book.id === entries.value[index].bookId), entries.value[index])) { toast('只能修改自己记录的账单'); return false }
      if (entries.value[index].bookId !== entry.bookId && (targetBook?.kind === 'shared' || books.value.find(book => book.id === entries.value[index].bookId)?.kind === 'shared')) { toast('多人账本的账单不能转移到其他账本'); return false }
      entries.value[index] = { ...entries.value[index], ...entry, createdBy: entries.value[index].createdBy }
    } else {
      entries.value.push({ ...entry, id: `entry-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, createdAt: Date.now(), createdBy: DEMO_USER_ID })
    }
    selectedMonth.value = entry.date.slice(0, 7)
    activeBookId.value = entry.bookId
    persist()
    return true
  }

  function deleteEntry(id: string, expectedVersion?: number): boolean | Promise<boolean> {
    const entry = entries.value.find(item => item.id === id)
    if (!entry) return false
    if (accountId.value) return cloudWrite(token => apiRequest(`/api/v1/entries/${id}?version=${expectedVersion ?? entry.version}`, { method: 'DELETE', token }), () => {
      entries.value = entries.value.filter(item => item.id !== id)
    })
    if (!canEditEntry(books.value.find(book => book.id === entry.bookId), entry)) { toast('只能删除自己记录的账单'); return false }
    entries.value = entries.value.filter(entry => entry.id !== id)
    persist()
    return true
  }

  function selectBook(id: string): void {
    activeBookId.value = id
    persist()
  }

  function addBook(name: string, shared = false): string | Promise<string> {
    if (accountId.value) {
      const key = `book:${name}`
      const id = pendingCreates.get(key) || newId()
      pendingCreates.set(key, id)
      let memberName = '创建者'
      try { memberName = String(uni.getStorageSync(preferenceKey('nickname')) || memberName).slice(0, 12) } catch { /* 使用默认称呼。 */ }
      return cloudWrite(token => apiRequest<Book>('/api/v1/books', { method: 'POST', token, data: { id, name, ...(shared ? { kind: 'shared', memberName } : {}) } }), book => {
        books.value = [...books.value.filter(item => item.id !== book.id), book]
        pendingCreates.delete(key)
      }).then(saved => saved ? id : '')
    }
    const id = `book-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    books.value.push(shared ? newSharedBook(name, id) : { id, name, description: '新的故事，从这一笔开始', icon: 'book', color: '#e1eee7', budget: 500000 })
    persist()
    return id
  }

  function updateBudget(amount: number, expectedVersion?: number): boolean | Promise<boolean> {
    if (!canManageBook(activeBook.value)) { toast('只有账本创建者可以设置共同预算'); return false }
    if (accountId.value) {
      const book = activeBook.value
      return cloudWrite(token => apiRequest<Book>(`/api/v1/books/${book.id}/budget`, { method: 'PUT', token, data: { budget: amount, version: expectedVersion ?? book.version } }), saved => {
        books.value = books.value.map(item => item.id === saved.id ? saved : item)
      })
    }
    if (!canManageBook(activeBook.value)) { toast('只有账本创建者可以设置共同预算'); return false }
    activeBook.value.budget = amount
    persist()
    return true
  }

  function openFamilyExample(asInvitee = false): string {
    if (accountId.value) { toast('多人账本暂为演示，请在体验模式中查看'); return '' }
    const example = familyExample(asInvitee)
    if (!books.value.some(book => book.id === example.book.id)) {
      books.value.push(example.book)
      entries.value.push(...example.entries)
    }
    activeBookId.value = example.book.id
    persist()
    return example.book.id
  }

  function createInvitation(bookId: string, replace = false): BookInvitation | undefined | Promise<BookInvitation | undefined> {
    if (accountId.value) {
      const key = `invite:${bookId}:${replace}`
      const id = pendingCreates.get(key) || newId()
      pendingCreates.set(key, id)
      let invitation: BookInvitation | undefined
      return cloudWrite(token => apiRequest<BookInvitation>(`/api/v1/books/${bookId}/invitations`, { method: 'POST', token, data: { id, replace } }), result => {
        invitation = result
        invitations.value = [...invitations.value.filter(item => item.bookId !== bookId), result]
        pendingCreates.delete(key)
      }).then(async saved => { if (saved) await refreshCloud(); return saved ? invitation : undefined })
    }
    const book = books.value.find(item => item.id === bookId)
    if (book?.kind !== 'shared' || !canManageBook(book)) return undefined
    const current = invitations.value.find(invitation => validInvitation(invitation, book))
    if (current && !replace) return current
    invitations.value.filter(invitation => invitation.bookId === bookId).forEach(invitation => { invitation.revoked = true })
    const invitation: BookInvitation = { token: `demo-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`, bookId, expiresAt: Date.now() + 7 * 24 * 60 * 60 * 1000, revoked: false }
    invitations.value.push(invitation)
    persist()
    return invitation
  }

  function revokeInvitation(token: string): boolean | Promise<boolean> {
    const invitation = invitations.value.find(item => item.token === token)
    if (accountId.value && invitation?.id) return cloudWrite(accessToken => apiRequest(`/api/v1/books/${invitation.bookId}/invitations/${invitation.id}`, { method: 'DELETE', token: accessToken }), () => {
      invitation.revoked = true
    }).then(async saved => { if (saved) await refreshCloud(); return saved })
    if (!invitation || !canManageBook(books.value.find(book => book.id === invitation.bookId))) return false
    invitation.revoked = true
    persist()
    return true
  }

  function removeMember(bookId: string, userId: string, expectedVersion?: number): boolean | Promise<boolean> {
    if (accountId.value) {
      const book = books.value.find(item => item.id === bookId)
      return cloudWrite(token => apiRequest<Book>(`/api/v1/books/${bookId}/members/${userId}?version=${expectedVersion ?? book?.version}`, { method: 'DELETE', token }), saved => {
        books.value = books.value.map(item => item.id === bookId ? saved : item)
        invitations.value.filter(item => item.bookId === bookId).forEach(item => { item.revoked = true })
      })
    }
    const book = books.value.find(item => item.id === bookId)
    const member = book?.members?.find(item => item.userId === userId)
    if (book?.kind !== 'shared' || !canManageBook(book) || !member || member.role === 'owner' || member.status !== 'active') return false
    member.status = 'removed'
    // 保留成员身份供历史账单展示，并撤销旧邀请，避免移出后仍可使用旧入口。
    invitations.value.filter(item => item.bookId === bookId).forEach(item => { item.revoked = true })
    persist()
    return true
  }

  async function acceptInvitation(token: string, name: string): Promise<string> {
    let id = ''
    const saved = await cloudWrite(accessToken => apiRequest<Book>('/api/v1/book-invitations/accept', { method: 'POST', token: accessToken, data: { token, name } }), book => {
      books.value = [...books.value.filter(item => item.id !== book.id), book]
      activeBookId.value = book.id
      id = book.id
    })
    if (saved) await refreshCloud()
    return saved ? id : ''
  }

  function purgeBook(id: string): void {
    books.value = books.value.filter(book => book.id !== id)
    entries.value = entries.value.filter(entry => entry.bookId !== id)
    invitations.value = invitations.value.filter(invite => invite.bookId !== id)
    if (activeBookId.value === id) activeBookId.value = books.value[0]?.id || 'daily'
  }

  function leaveBook(id: string, close = false, expectedVersion?: number): Promise<boolean> {
    const book = books.value.find(item => item.id === id)
    return cloudWrite(token => apiRequest(`/api/v1/books/${id}/${close ? 'close' : 'leave'}`, { method: 'POST', token, data: { version: expectedVersion ?? book?.version } }), () => { purgeBook(id) })
  }

  function transferBook(id: string, userId: string, expectedVersion?: number): Promise<boolean> {
    const book = books.value.find(item => item.id === id)
    return cloudWrite(token => apiRequest<Book>(`/api/v1/books/${id}/transfer`, { method: 'POST', token, data: { version: expectedVersion ?? book?.version, userId } }), result => {
      books.value = books.value.map(item => item.id === id ? result : item)
      invitations.value = invitations.value.filter(item => item.bookId !== id)
    })
  }

  function bookActivities(id: string, before?: string): Promise<BookActivity[]> {
    return authorizedRead(`/api/v1/books/${id}/activities${before ? `?before=${before}` : ''}`)
  }

  function resetDemo(): void {
    if (accountId.value) { toast('请在体验模式中恢复演示数据'); return }
    entries.value = createDemoEntries()
    books.value = initialBooks.map(book => ({ ...book }))
    invitations.value = []
    activeBookId.value = 'daily'
    selectedMonth.value = localDate().slice(0, 7)
    persist()
  }

  function categoryOf(id: string) {
    return categories.find(category => category.id === id) || categories[11]
  }

  return { entries, books, invitations, accountId, activeBookId, selectedMonth, storageAvailable, syncing, writing, cloudReady, syncError, legacyEntries, activeBook, bookEntries, monthEntries, expense, income, balance, hydrate, switchAccount, refreshCloud, recognizeDrafts, recordDrafts, chatRequest, streamChat, preferenceKey, saveEntry, deleteEntry, selectBook, addBook, updateBudget, openFamilyExample, createInvitation, revokeInvitation, removeMember, acceptInvitation, leaveBook, transferBook, bookActivities, resetDemo, categoryOf }
})
