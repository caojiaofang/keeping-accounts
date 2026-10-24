import type { AnalysisResult } from '@/common/mock/assistant'

export interface ChatContext {
  month: string
  categoryId?: string
  entryType: 'expense' | 'income'
}

export interface ChatTurn {
  id: string
  sequence: number
  text: string
  answer: string
  result?: AnalysisResult
  context: ChatContext
  createdAt: number
}

export interface ConversationSummary {
  id: string
  bookId: string
  month: string
  title: string
  updatedAt: number
}

export interface Conversation extends ConversationSummary {
  turns: ChatTurn[]
  hasMore: boolean
}
