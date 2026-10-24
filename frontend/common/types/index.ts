export type EntryType = 'expense' | 'income'

export interface Category {
  id: string
  name: string
  icon: string
  color: string
  type: EntryType
}

export interface Book {
  ownerId?: string
  currentUserId?: string
  version?: number
  id: string
  name: string
  description: string
  icon: string
  color: string
  budget: number
  kind?: 'personal' | 'shared'
  members?: BookMember[]
}

export interface BookMember {
  userId: string
  name: string
  color: string
  role: 'owner' | 'member'
  status: 'active' | 'removed' | 'left'
}

export interface BookInvitation {
  id?: string
  token: string
  bookId: string
  expiresAt: number
  revoked: boolean
}

export interface Entry {
  updatedBy?: string
  updatedAt?: number
  version?: number
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
  createdBy?: string
}

export interface InvitationPreview {
  bookId: string
  bookName: string
  inviterName: string
  expiresAt: number
}

export interface BookActivity {
  id: string
  actorId: string
  action: string
  targetId?: string
  payload: Record<string, unknown>
  createdAt: number
}
