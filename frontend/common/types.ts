export type EntryType = 'expense' | 'income'

export interface Category {
  id: string
  name: string
  icon: string
  color: string
  type: EntryType
}

export interface Book {
  id: string
  name: string
  description: string
  icon: string
  color: string
  budget: number
}

export interface Entry {
  id: string
  bookId: string
  type: EntryType
  categoryId: string
  amount: number
  date: string
  time?: string
  note: string
  account: string
  createdAt: number
}
