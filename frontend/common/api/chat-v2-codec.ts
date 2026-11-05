import type { AnswerPreviewV2, ChatContextV2, ChatStreamEventV2, ChatTurnV2, ConversationSummaryV2, ConversationV2, FactRefV2 } from '@/common/types/chat-v2'

type Obj = Record<string, unknown>
const error = () => new Error('分析服务返回的数据不完整，请重新读取或重试')
function record(value: unknown): asserts value is Obj { if (!value || typeof value !== 'object' || Array.isArray(value)) throw error() }
function text(value: unknown, max = 2500): asserts value is string { if (typeof value !== 'string' || value.length > max) throw error() }
function integer(value: unknown, min = 0): asserts value is number { if (!Number.isSafeInteger(value) || (value as number) < min) throw error() }
function values(value: unknown, options: readonly unknown[]): void { if (!options.includes(value)) throw error() }
function list(value: unknown, check: (item: unknown) => void, max = 2000): asserts value is unknown[] { if (!Array.isArray(value) || value.length > max) throw error(); value.forEach(check) }
function strings(value: unknown, max = 2000): asserts value is string[] { list(value, item => text(item, 2048), max) }
function uuid(value: unknown): void { if (typeof value !== 'string' || !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value)) throw error() }
function bool(value: unknown): void { if (typeof value !== 'boolean') throw error() }
function day(value: unknown): asserts value is string {
  if (typeof value !== 'string' || !/^(20\d{2}|2100)-\d{2}-\d{2}$/.test(value)) throw error()
  const parsed = new Date(value + 'T00:00:00Z')
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) throw error()
}
function range(value: unknown): void { record(value); day(value.start); day(value.end); if (value.start > value.end) throw error() }
function optional(value: unknown, check: (value: unknown) => void): void { if (value !== undefined && value !== null) check(value) }
function unique(items: unknown[], key: (item: unknown) => unknown = item => item): void { if (new Set(items.map(key)).size !== items.length) throw error() }
function reference(value: unknown): void { record(value); uuid(value.turnId); text(value.cardId, 96); list(value.entryIds, uuid, 100); unique(value.entryIds) }
function filters(value: unknown): void {
  record(value); range(value.dateRange); values(value.entryType, ['expense', 'income', 'both'])
  strings(value.categoryIds, 32); strings(value.accounts, 20); list(value.creatorIds, uuid, 50)
  if (value.amountMin !== null) integer(value.amountMin)
  if (value.amountMax !== null) integer(value.amountMax)
  if (value.amountMin !== null && value.amountMax !== null && Number(value.amountMin) > Number(value.amountMax)) throw error()
  bool(value.minInclusive); bool(value.maxInclusive)
  list(value.weekdays, item => { integer(item, 1); if (item > 7) throw error() }, 7)
  list(value.excludedEntryIds, uuid, 100)
  record(value.keywords); strings(value.keywords.include, 20); strings(value.keywords.exclude, 20); values(value.keywords.mode, ['any', 'all'])
}
function context(value: unknown): asserts value is ChatContextV2 {
  record(value); values(value.schemaVersion, [2]); filters(value.filters); optional(value.previousRange, range)
  list(value.sources, source => { record(source); text(source.field, 96); values(source.source, ['explicit', 'inherited', 'default']); optional(source.restoredFrom, uuid) })
  list(value.resultRefs, reference, 20); text(value.summary, 2000); integer(value.summaryThroughSequence)
}
function fact(value: unknown): void {
  record(value); text(value.id, 96); if (!/^[A-Za-z][A-Za-z0-9_.-]*$/.test(value.id)) throw error()
  text(value.sourceCallId, 96); if (!value.sourceCallId || !value.id.startsWith(value.sourceCallId + '.')) throw error()
  text(value.label, 120); values(value.unit, ['cents', 'count', 'basis_points', 'date', 'text'])
  if (['cents', 'count', 'basis_points'].includes(String(value.unit))) integer(value.value, value.unit === 'count' ? 0 : -Number.MAX_SAFE_INTEGER)
  else { text(value.value); if (value.unit === 'date') day(value.value) }
  optional(value.metric, item => values(item, ['total', 'count', 'previous_total', 'previous_count', 'difference', 'change_rate', 'share', 'amount']))
  optional(value.dateRange, range); optional(value.previousRange, range); optional(value.generatedAt, item => integer(item))
}
export function parsePreviewV2(value: unknown, turnId?: string): AnswerPreviewV2 {
  record(value); values(value.schemaVersion, [2]); text(value.answer); values(value.completeness, ['complete', 'partial'])
  strings(value.missing); if ((value.completeness === 'partial') !== !!value.missing.length) throw error()
  list(value.factRefs, fact, 10000); unique(value.factRefs, item => (item as Obj).id)
  const facts = new Set(value.factRefs.map(item => (item as Obj).id))
  const refs = (ids: unknown) => { strings(ids); if (ids.some(id => !facts.has(id))) throw error() }
  list(value.cards, card => {
    record(card); text(card.id, 96); text(card.title, 120); values(card.type, ['summary', 'entries', 'trend', 'comparison'])
    filters(card.filters); optional(card.previousRange, range); if (card.type === 'comparison' && !card.previousRange) throw error()
    integer(card.generatedAt); refs(card.factIds); text(card.footnote, 500)
    list(card.rows, row => {
      record(row); text(row.label, 120); refs(row.factIds)
      optional(row.entryId, uuid); optional(row.date, day); optional(row.detail, item => text(item, 300)); optional(row.creatorId, uuid)
      optional(row.entryType, item => values(item, ['expense', 'income'])); optional(row.account, item => text(item, 24)); optional(row.categoryId, item => text(item, 24))
      optional(row.groupKey, item => text(item, 120)); optional(row.matchedKeywords, item => strings(item, 20)); optional(row.isOther, bool)
      optional(row.time, item => { if (typeof item !== 'string' || !/^([01]\d|2[0-3]):[0-5]\d$/.test(item)) throw error() })
      if (card.type === 'entries' && ['entryId', 'date', 'entryType', 'categoryId', 'account', 'creatorId'].some(key => row[key] === null || row[key] === undefined)) throw error()
      if (card.type === 'trend' && !row.date) throw error()
    }, 1000)
  }, 30)
  unique(value.cards, item => (item as Obj).id)
  list(value.suggestions, suggestion => {
    record(suggestion); uuid(suggestion.id); uuid(suggestion.sourceTurnId); text(suggestion.text, 200)
    if (turnId && suggestion.sourceTurnId !== turnId) throw error()
    optional(suggestion.reference, reference)
    if (turnId && suggestion.reference && (suggestion.reference as Obj).turnId === turnId) {
      const ref = suggestion.reference as Obj
      const card = (value.cards as Obj[]).find(card => card.id === ref.cardId)
      if (!card || (ref.entryIds as string[]).some(id => !(card.rows as Obj[]).some(row => row.entryId === id))) throw error()
    }
  }, 3)
  unique(value.suggestions, item => (item as Obj).id)
  return value as unknown as AnswerPreviewV2
}
export function parseTurnV2(value: unknown): ChatTurnV2 {
  record(value); uuid(value.id); integer(value.sequence, 1); text(value.text, 200); integer(value.createdAt); context(value.context)
  parsePreviewV2(value, value.id as string)
  return value as unknown as ChatTurnV2
}
export function parseSummaryV2(value: unknown): ConversationSummaryV2 {
  record(value); values(value.schemaVersion, [2]); uuid(value.id); uuid(value.bookId); text(value.title, 40); integer(value.updatedAt)
  if (typeof value.initialMonth !== 'string' || !/^(20\d{2}|2100)-(0[1-9]|1[0-2])$/.test(value.initialMonth)) throw error()
  return value as unknown as ConversationSummaryV2
}
export function parseConversationV2(value: unknown): ConversationV2 {
  parseSummaryV2(value); record(value); context(value.context); bool(value.hasMore); list(value.turns, parseTurnV2, 20)
  unique(value.turns, item => (item as Obj).id); unique(value.turns, item => (item as Obj).sequence)
  if (value.turns.some((item, i, all) => i > 0 && (item as Obj).sequence! <= (all[i - 1] as Obj).sequence!)) throw error()
  return value as unknown as ConversationV2
}
export function parseCapabilitiesV2(value: unknown): boolean {
  record(value); bool(value.enabled); list(value.schemaVersions, item => integer(item, 1), 10)
  if (value.enabled && !value.schemaVersions.includes(2)) throw error()
  return value.enabled as boolean
}
export function decodeEventV2(event: string, value: unknown, turnId?: string): ChatStreamEventV2 | undefined {
  record(value)
  if (event === 'ping') return undefined
  if (event === 'snapshot') return { event, data: parsePreviewV2(value, turnId) }
  if (event === 'done') {
    const data = parseTurnV2(value)
    if (turnId && data.id !== turnId) throw error()
    return { event, data }
  }
  if (event === 'status' || event === 'delta') { text(value.text, 2500); return { event, data: { text: value.text } } }
  if (event === 'error') { integer(value.status, 400); if (value.status > 599) throw error(); text(value.detail); return { event, data: { status: value.status, detail: value.detail } } }
  throw error()
}
export function formatFactV2(fact: FactRefV2): string {
  if (typeof fact.value === 'string') return fact.value
  const abs = Math.abs(fact.value), sign = fact.value < 0 ? '−' : ''
  const decimal = `${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, '0')}`
  if (fact.unit === 'cents') return `${sign}¥${decimal.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}`
  if (fact.unit === 'basis_points') return `${sign}${decimal}%`
  return `${fact.value} 笔`
}
