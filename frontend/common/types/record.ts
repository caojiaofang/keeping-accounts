import type { AssistantDraft } from '@/common/mock/assistant'

export interface RecordImage {
  path: string
  dataUrl: string
}

export interface RecordHistory {
  role: 'user' | 'assistant'
  text: string
}

export interface RecordInput {
  text: string
  history: RecordHistory[]
  drafts: Omit<AssistantDraft, 'status'>[]
  image?: string
}

export interface RecordResult {
  drafts: AssistantDraft[]
  clarification?: string
  reply: string
}

export interface RecordMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  imagePath?: string
  drafts?: AssistantDraft[]
  superseded?: boolean
  failed?: boolean
}
