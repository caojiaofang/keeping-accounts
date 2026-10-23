import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { categories, createDemoEntries, initialBooks } from '@/common/mock'
import type { Book, Entry } from '@/common/types'
import { localDate } from '@/common/format'

const STORAGE_KEY = 'moji-demo-v1'

interface StoredState {
  entries: Entry[]
  books: Book[]
  activeBookId: string
}

export const useLedgerStore = defineStore('ledger', () => {
  const entries = ref<Entry[]>([])
  const books = ref<Book[]>(initialBooks.map(book => ({ ...book })))
  const activeBookId = ref('daily')
  const selectedMonth = ref(localDate().slice(0, 7))
  const storageAvailable = ref(true)
  let initialized = false

  const activeBook = computed(() => books.value.find(book => book.id === activeBookId.value) || books.value[0])
  const bookEntries = computed(() => entries.value.filter(entry => entry.bookId === activeBookId.value))
  const monthEntries = computed(() => bookEntries.value.filter(entry => entry.date.startsWith(selectedMonth.value)))
  const expense = computed(() => monthEntries.value.filter(entry => entry.type === 'expense').reduce((sum, entry) => sum + entry.amount, 0))
  const income = computed(() => monthEntries.value.filter(entry => entry.type === 'income').reduce((sum, entry) => sum + entry.amount, 0))
  const balance = computed(() => income.value - expense.value)

  function persist(): void {
    try {
      uni.setStorageSync(STORAGE_KEY, { entries: entries.value, books: books.value, activeBookId: activeBookId.value })
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
      const saved = uni.getStorageSync(STORAGE_KEY) as StoredState | undefined
      if (saved && Array.isArray(saved.entries) && Array.isArray(saved.books) && saved.books.length) {
        entries.value = saved.entries
        books.value = saved.books
        activeBookId.value = saved.books.some(book => book.id === saved.activeBookId) ? saved.activeBookId : saved.books[0].id
        return
      }
    } catch {
      storageAvailable.value = false
    }
    entries.value = createDemoEntries()
    persist()
  }

  function saveEntry(entry: Omit<Entry, 'id' | 'createdAt'>, id?: string): void {
    if (id) {
      const index = entries.value.findIndex(item => item.id === id)
      if (index >= 0) entries.value[index] = { ...entries.value[index], ...entry }
    } else {
      entries.value.push({ ...entry, id: `entry-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, createdAt: Date.now() })
    }
    selectedMonth.value = entry.date.slice(0, 7)
    activeBookId.value = entry.bookId
    persist()
  }

  function deleteEntry(id: string): void {
    entries.value = entries.value.filter(entry => entry.id !== id)
    persist()
  }

  function selectBook(id: string): void {
    activeBookId.value = id
    persist()
  }

  function addBook(name: string): void {
    books.value.push({ id: `book-${Date.now()}`, name, description: '新的故事，从这一笔开始', icon: 'book', color: '#e1eee7', budget: 500000 })
    persist()
  }

  function updateBudget(amount: number): void {
    activeBook.value.budget = amount
    persist()
  }

  function resetDemo(): void {
    entries.value = createDemoEntries()
    books.value = initialBooks.map(book => ({ ...book }))
    activeBookId.value = 'daily'
    selectedMonth.value = localDate().slice(0, 7)
    persist()
  }

  function categoryOf(id: string) {
    return categories.find(category => category.id === id) || categories[11]
  }

  return { entries, books, activeBookId, selectedMonth, storageAvailable, activeBook, bookEntries, monthEntries, expense, income, balance, hydrate, saveEntry, deleteEntry, selectBook, addBook, updateBudget, resetDemo, categoryOf }
})
