import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { categories } from '@/common/data/categories'
import { createDemoEntries, initialBooks } from '@/common/mock/ledger'
import type { Book, BookInvitation, Entry } from '@/common/types'
import { localDate, toast } from '@/common/utils/format'
import { canEditEntry, canManageBook, canWriteBook, DEMO_USER_ID, familyExample, newSharedBook, validInvitation } from '@/common/services/shared-books'

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
  let initialized = false

  function preferenceKey(name: string): string {
    if (accountId.value) return `moji-user-v1:${accountId.value}:${name}`
    return name === 'ledger' ? STORAGE_KEY : `moji-${name}`
  }

  function switchAccount(userId: string | null): void {
    if (initialized && accountId.value === userId) return
    accountId.value = userId
    entries.value = []
    books.value = (userId ? initialBooks.slice(0, 1) : initialBooks).map(book => ({ ...book }))
    invitations.value = []
    activeBookId.value = 'daily'
    selectedMonth.value = localDate().slice(0, 7)
    initialized = false
    hydrate()
  }

  const activeBook = computed(() => books.value.find(book => book.id === activeBookId.value) || books.value[0])
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
      uni.showToast({ title: '本地空间不足，本次更改仅在当前运行中保留', icon: 'none' })
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

  function saveEntry(entry: Omit<Entry, 'id' | 'createdAt' | 'createdBy'>, id?: string): boolean {
    const targetBook = books.value.find(book => book.id === entry.bookId)
    if (!canWriteBook(targetBook)) { toast('你还不是这个账本的成员'); return false }
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

  function deleteEntry(id: string): boolean {
    const entry = entries.value.find(item => item.id === id)
    if (!entry) return false
    if (!canEditEntry(books.value.find(book => book.id === entry.bookId), entry)) { toast('只能删除自己记录的账单'); return false }
    entries.value = entries.value.filter(entry => entry.id !== id)
    persist()
    return true
  }

  function selectBook(id: string): void {
    activeBookId.value = id
    persist()
  }

  function addBook(name: string, shared = false): string {
    const id = `book-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    books.value.push(shared ? newSharedBook(name, id) : { id, name, description: '新的故事，从这一笔开始', icon: 'book', color: '#e1eee7', budget: 500000 })
    persist()
    return id
  }

  function updateBudget(amount: number): boolean {
    if (!canManageBook(activeBook.value)) { toast('只有账本创建者可以设置共同预算'); return false }
    activeBook.value.budget = amount
    persist()
    return true
  }

  function openFamilyExample(asInvitee = false): string {
    const example = familyExample(asInvitee)
    if (!books.value.some(book => book.id === example.book.id)) {
      books.value.push(example.book)
      entries.value.push(...example.entries)
    }
    activeBookId.value = example.book.id
    persist()
    return example.book.id
  }

  function createInvitation(bookId: string, replace = false): BookInvitation | undefined {
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

  function revokeInvitation(token: string): boolean {
    const invitation = invitations.value.find(item => item.token === token)
    if (!invitation || !canManageBook(books.value.find(book => book.id === invitation.bookId))) return false
    invitation.revoked = true
    persist()
    return true
  }

  function removeMember(bookId: string, userId: string): boolean {
    const book = books.value.find(item => item.id === bookId)
    const member = book?.members?.find(item => item.userId === userId)
    if (book?.kind !== 'shared' || !canManageBook(book) || !member || member.role === 'owner' || member.status !== 'active') return false
    member.status = 'removed'
    // 保留成员身份供历史账单展示，并撤销旧邀请，避免移出后仍可使用旧入口。
    invitations.value.filter(item => item.bookId === bookId).forEach(item => { item.revoked = true })
    persist()
    return true
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

  return { entries, books, invitations, accountId, activeBookId, selectedMonth, storageAvailable, activeBook, bookEntries, monthEntries, expense, income, balance, hydrate, switchAccount, preferenceKey, saveEntry, deleteEntry, selectBook, addBook, updateBudget, openFamilyExample, createInvitation, revokeInvitation, removeMember, resetDemo, categoryOf }
})
