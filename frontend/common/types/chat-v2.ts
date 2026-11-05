export interface DateRangeV2 { start: string; end: string }
export interface EntryFilterV2 {
  dateRange: DateRangeV2
  entryType: 'expense' | 'income' | 'both'
  categoryIds: string[]
  keywords: { include: string[]; mode: 'any' | 'all'; exclude: string[] }
  accounts: string[]
  creatorIds: string[]
  amountMin: number | null
  amountMax: number | null
  minInclusive: boolean
  maxInclusive: boolean
  weekdays: number[]
  excludedEntryIds: string[]
}
export interface ResultReferenceV2 { turnId: string; cardId: string; entryIds: string[] }
export interface ChatContextV2 {
  schemaVersion: 2
  filters: EntryFilterV2
  previousRange: DateRangeV2 | null
  sources: { field: string; source: 'explicit' | 'inherited' | 'default'; restoredFrom?: string | null }[]
  resultRefs: ResultReferenceV2[]
  summary: string
  summaryThroughSequence: number
}
export interface FactRefV2 {
  id: string
  value: number | string
  unit: 'cents' | 'count' | 'basis_points' | 'date' | 'text'
  label: string
  sourceCallId: string
  metric?: 'total' | 'count' | 'previous_total' | 'previous_count' | 'difference' | 'change_rate' | 'share' | 'amount' | null
  dateRange?: DateRangeV2 | null
  previousRange?: DateRangeV2 | null
  generatedAt?: number | null
}
export interface ResultRowV2 {
  label: string; factIds: string[]
  entryId?: string | null; date?: string | null; detail?: string | null
  entryType?: 'expense' | 'income' | null; groupKey?: string | null; categoryId?: string | null
  account?: string | null; creatorId?: string | null; time?: string | null
  matchedKeywords?: string[]; isOther?: boolean
}
export interface ResultCardV2 {
  id: string; type: 'summary' | 'entries' | 'trend' | 'comparison'; title: string
  filters: EntryFilterV2; previousRange: DateRangeV2 | null; generatedAt: number
  factIds: string[]; rows: ResultRowV2[]; footnote: string
}
export interface SuggestionV2 { id: string; text: string; sourceTurnId: string; reference?: ResultReferenceV2 | null }
export interface AnswerPreviewV2 {
  schemaVersion: 2; answer: string; completeness: 'complete' | 'partial'; missing: string[]
  cards: ResultCardV2[]; factRefs: FactRefV2[]; suggestions: SuggestionV2[]
}
export interface ChatTurnV2 extends AnswerPreviewV2 {
  id: string; sequence: number; text: string; context: ChatContextV2; createdAt: number
}
export interface ConversationSummaryV2 {
  schemaVersion: 2; id: string; bookId: string; initialMonth: string; title: string; updatedAt: number
}
export interface ConversationV2 extends ConversationSummaryV2 {
  context: ChatContextV2; turns: ChatTurnV2[]; hasMore: boolean
}
export type ChatStreamEventV2 =
  | { event: 'status'; data: { text: string } }
  | { event: 'snapshot'; data: AnswerPreviewV2 }
  | { event: 'delta'; data: { text: string } }
  | { event: 'done'; data: ChatTurnV2 }
  | { event: 'error'; data: { status: number; detail: string } }
