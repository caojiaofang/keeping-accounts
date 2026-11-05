import { computed, ref, watch } from 'vue'
import { defineStore } from 'pinia'
import { useLedgerStore } from '@/store/ledger'
import { newId } from '@/common/utils/id'
import { ApiError } from '@/common/api/request'
import { parseCapabilitiesV2, parseConversationV2, parseSummaryV2 } from '@/common/api/chat-v2-codec'
import type { ChatContext, ChatTurn, Conversation, ConversationSummary } from '@/common/types/chat'
import type { AnswerPreviewV2, ChatContextV2, ChatTurnV2, ConversationSummaryV2, SuggestionV2 } from '@/common/types/chat-v2'

type Protocol = 'unknown' | 'v1' | 'v2'
type Turn = ChatTurn | ChatTurnV2
type Summary = ConversationSummary | ConversationSummaryV2
type Preview = Pick<ChatTurn, 'answer' | 'result'> | AnswerPreviewV2
interface Pending { id: string; text: string; protocol: Exclude<Protocol, 'unknown'>; suggestionId?: string }
export const isV2Turn = (turn: Turn): turn is ChatTurnV2 => 'schemaVersion' in turn && turn.schemaVersion === 2

export const useChatStore = defineStore('chat', () => {
  const ledger = useLedgerStore()
  const turns = ref<Turn[]>([]), history = ref<Summary[]>([])
  const legacyHistory = ref<ConversationSummary[]>([]), legacyReadonly = ref(false)
  const conversationId = ref(''), initialMonth = ref(''), bookId = ref('')
  const ownerId = ref<string | null>(null)
  const protocol = ref<Protocol>('unknown'), ready = ref(false)
  const loading = ref(false), sending = ref(false), error = ref(''), progress = ref('')
  const pending = ref<Pending>(), preview = ref<Preview>()
  const hasMore = ref(false), contextV2 = ref<ChatContextV2>()
  const context = computed<ChatContext | undefined>(() => {
    const turn = turns.value[turns.value.length - 1]
    return turn && !isV2Turn(turn) ? turn.context : undefined
  })
  const suggestions = computed(() => {
    const turn = turns.value[turns.value.length - 1]
    return turn && isV2Turn(turn) && turn.completeness === 'complete' ? turn.suggestions : []
  })
  const busy = computed(() => loading.value || sending.value)
  let generation = 0, created = false, creationMonth = '', updatedAt = 0
  let cancelStream: (() => void) | undefined
  let forkRequest: { source: string; id: string; initialMonth: string } | undefined
  watch(() => ledger.accountId, clear, { flush: 'sync' })
  watch(() => ledger.activeBookId, clear, { flush: 'sync' })
  watch(() => ledger.cloudReady, value => { if (!value) clear() }, { flush: 'sync' })

  function clear(): void {
    generation++
    cancelStream?.(); cancelStream = undefined
    turns.value = []; history.value = []; preview.value = undefined; pending.value = undefined
    conversationId.value = ''; bookId.value = ''; initialMonth.value = ''; ownerId.value = null
    loading.value = false; sending.value = false; error.value = ''; progress.value = ''
    hasMore.value = false; contextV2.value = undefined; protocol.value = 'unknown'; ready.value = false
    created = false; creationMonth = ''; updatedAt = 0
    legacyHistory.value = []; legacyReadonly.value = false; forkRequest = undefined
  }
  // 离开页面或切到记账模式只取消当前任务，保留未确认消息，不能误当作发送成功。
  function suspend(): void {
    generation++; cancelStream?.(); cancelStream = undefined
    loading.value = false; sending.value = false; progress.value = ''
    if (pending.value) error.value = '回答尚未确认保存，可重新读取或重试原消息'
  }
  function current(version: number): boolean {
    return version === generation && ownerId.value === ledger.accountId && bookId.value === ledger.activeBookId
  }
  function report(cause: unknown): void { error.value = cause instanceof Error ? cause.message : '对话暂时不可用，请重试' }
  function path(value: string): string { return protocol.value === 'v2' ? `/v2${value}` : value }
  function merge(old: Turn[], incoming: Turn[]): Turn[] {
    const result = new Map(old.map(turn => [turn.id, turn]))
    for (const turn of incoming) {
      const existing = result.get(turn.id)
      if (existing && (existing.sequence !== turn.sequence || existing.text !== turn.text)) throw new Error('消息历史不一致，请重新读取')
      result.set(turn.id, turn)
    }
    const all = [...result.values()].sort((a, b) => a.sequence - b.sequence)
    if (new Set(all.map(turn => turn.sequence)).size !== all.length) throw new Error('对话序号不一致，请重新读取')
    return all
  }
  async function refreshHistory(): Promise<boolean> {
    if (protocol.value === 'unknown') return false
    const version = generation
    try {
      const query = `?bookId=${encodeURIComponent(bookId.value)}${protocol.value === 'v1' ? `&month=${initialMonth.value}` : ''}`
      const raw = await ledger.chatRequest<unknown>(path('/conversations') + query)
      if (!current(version)) return false
      if (!Array.isArray(raw) || raw.length > 20) throw new Error('历史列表不完整，请重试')
      const result: Summary[] = protocol.value === 'v2' ? raw.map(parseSummaryV2) : raw
      if (result.some(item => item.bookId !== bookId.value) || new Set(result.map(item => item.id)).size !== result.length) throw new Error('历史账本不一致，请重新读取')
      history.value = result
      return true
    } catch (cause) { if (current(version)) report(cause); return false }
  }
  async function read(id: string): Promise<boolean> {
    if (busy.value || protocol.value === 'unknown') return false
    if (pending.value && id !== conversationId.value) { error.value = '请先确认上一条消息，或开始新对话'; return false }
    const version = ++generation
    loading.value = true; error.value = ''
    try {
      const raw = await ledger.chatRequest<unknown>(path(`/conversations/${encodeURIComponent(id)}`))
      if (!current(version)) return false
      const result = protocol.value === 'v2' ? parseConversationV2(raw) : raw as Conversation
      if (result.id !== id || result.bookId !== bookId.value || ('month' in result && result.month !== initialMonth.value)) throw new Error('会话范围不一致，请重新选择')
      const confirmed = pending.value ? result.turns.find(turn => turn.id === pending.value!.id) : undefined
      if (confirmed && confirmed.text !== pending.value?.text) throw new Error('消息内容不一致，请重新读取')
      turns.value = merge([], result.turns)
      conversationId.value = id; hasMore.value = result.hasMore; created = true
      legacyReadonly.value = false
      creationMonth = 'initialMonth' in result ? result.initialMonth : result.month
      updatedAt = result.updatedAt
      ready.value = true
      contextV2.value = 'schemaVersion' in result ? result.context : undefined
      if (!pending.value || confirmed) { pending.value = undefined; preview.value = undefined }
      else error.value = '尚未读到原消息的保存结果，可保留原编号重试'
      return true
    } catch (cause) { if (current(version)) report(cause); return false }
    finally { if (current(version)) loading.value = false }
  }
  async function load(month: string, force = false): Promise<void> {
    if (!force && pending.value && ownerId.value === ledger.accountId && bookId.value === ledger.activeBookId) {
      if (protocol.value === 'v2') initialMonth.value = month
      return
    }
    if (!force && protocol.value === 'v2' && ready.value && ownerId.value === ledger.accountId && bookId.value === ledger.activeBookId) {
      initialMonth.value = month
      return
    }
    clear()
    if (!ledger.accountId || !ledger.cloudReady) return
    initialMonth.value = month; bookId.value = ledger.activeBookId; ownerId.value = ledger.accountId
    const version = generation
    loading.value = true
    try {
      let next: Protocol
      try { next = parseCapabilitiesV2(await ledger.chatRequest<unknown>('/v2/capabilities')) ? 'v2' : 'v1' }
      catch (cause) { if (cause instanceof ApiError && cause.status === 404) next = 'v1'; else throw cause }
      if (!current(version)) return
      protocol.value = next
      if (!await refreshHistory() || !current(version)) return
      ready.value = true
    } catch (cause) { if (current(version)) report(cause) }
    finally { if (current(version)) loading.value = false }
    if (current(version) && ready.value && history.value.length) await read(history.value[0].id)
  }
  function startNew(): void {
    if (busy.value || !ready.value) return
    generation++; conversationId.value = ''; turns.value = []; contextV2.value = undefined
    pending.value = undefined; preview.value = undefined; progress.value = ''; error.value = ''; hasMore.value = false
    created = false; creationMonth = ''; updatedAt = 0
    legacyReadonly.value = false; forkRequest = undefined
  }
  async function refreshLegacyHistory(): Promise<boolean> {
    if (protocol.value !== 'v2') return false
    const version = generation
    try {
      const result = await ledger.chatRequest<ConversationSummary[]>(`/conversations?bookId=${encodeURIComponent(bookId.value)}&month=${initialMonth.value}`)
      if (!current(version)) return false
      if (!Array.isArray(result) || result.some(item => item.bookId !== bookId.value || item.month !== initialMonth.value)) throw new Error('旧版历史范围不一致，请重试')
      legacyHistory.value = result
      return true
    } catch (cause) { if (current(version)) report(cause); return false }
  }
  async function readLegacy(id: string): Promise<boolean> {
    if (busy.value || protocol.value !== 'v2' || pending.value) return false
    const version = ++generation
    loading.value = true; error.value = ''
    try {
      // 保留旧接口的原始统计卡，展示只读快照；继续提问必须显式 fork。
      const result = await ledger.chatRequest<Conversation>(`/conversations/${encodeURIComponent(id)}`)
      if (!current(version)) return false
      if (result.id !== id || result.bookId !== bookId.value || !Array.isArray(result.turns) || result.turns.some(turn => isV2Turn(turn))) throw new Error('旧版对话不一致，请重新读取')
      turns.value = merge([], result.turns); conversationId.value = id; hasMore.value = result.hasMore
      contextV2.value = undefined; legacyReadonly.value = true; ready.value = true
      preview.value = undefined; created = true; creationMonth = result.month; updatedAt = result.updatedAt; forkRequest = undefined
      return true
    } catch (cause) { if (current(version)) report(cause); return false }
    finally { if (current(version)) loading.value = false }
  }
  async function startFromLegacy(): Promise<boolean> {
    if (busy.value || !legacyReadonly.value || pending.value) return false
    const version = ++generation, source = conversationId.value
    if (forkRequest?.source !== source) forkRequest = { source, id: newId(), initialMonth: initialMonth.value }
    const target = forkRequest.id
    loading.value = true; error.value = ''
    try {
      const result = parseSummaryV2(await ledger.chatRequest<unknown>(`/v2/legacy/conversations/${encodeURIComponent(source)}/fork`, {
        method: 'POST', data: { id: target, bookId: bookId.value, initialMonth: forkRequest.initialMonth },
      }))
      if (!current(version)) return false
      if (result.id !== target || result.bookId !== bookId.value) throw new Error('新会话范围不一致，请重试')
    } catch (cause) { if (current(version)) report(cause); return false }
    finally { if (current(version)) loading.value = false }
    if (!current(version)) return false
    const loaded = await read(target)
    if (loaded) forkRequest = undefined
    return loaded
  }
  async function loadEarlier(): Promise<void> {
    if (busy.value || !hasMore.value || !turns.value.length) return
    const version = generation
    loading.value = true; error.value = ''
    try {
      const before = turns.value[0].sequence
      const endpoint = `/conversations/${encodeURIComponent(conversationId.value)}?before=${before}`
      const raw = await ledger.chatRequest<unknown>(legacyReadonly.value ? endpoint : path(endpoint))
      if (!current(version)) return
      const result = protocol.value === 'v2' && !legacyReadonly.value ? parseConversationV2(raw) : raw as Conversation
      if (result.id !== conversationId.value || result.bookId !== bookId.value || result.turns.some(turn => turn.sequence >= before)) throw new Error('历史页范围不一致，请重新读取')
      turns.value = merge(turns.value, result.turns); hasMore.value = result.hasMore
      // 使用服务端当前权威上下文，不能从较早历史消息推导当前筛选。
      if ('schemaVersion' in result && result.updatedAt >= updatedAt) { contextV2.value = result.context; updatedAt = result.updatedAt }
    } catch (cause) { if (current(version)) report(cause) }
    finally { if (current(version)) loading.value = false }
  }
  async function send(text: string, suggestionId?: string): Promise<boolean> {
    const question = text.trim()
    if (!question || question.length > 200 || busy.value) return false
    if (legacyReadonly.value) { error.value = '旧版对话为只读，请先基于此对话开始新版续聊'; return false }
    if (!ready.value || protocol.value === 'unknown' || !ownerId.value || !current(generation)) { error.value = '请先重新读取当前账号的对话'; return false }
    if (suggestionId && protocol.value !== 'v2') { error.value = '这条建议不属于当前对话'; return false }
    if (pending.value && (pending.value.text !== question || pending.value.suggestionId !== suggestionId || pending.value.protocol !== protocol.value)) { error.value = '上一条消息尚未确认，请先重试或重新读取对话'; return false }
    const version = generation
    sending.value = true; error.value = ''; progress.value = '正在连接分析服务…'
    // 重试保留未确认预览和原请求，不让新快照与旧卡片累加。
    pending.value ||= { id: newId(), text: question, protocol: protocol.value, ...(suggestionId ? { suggestionId } : {}) }
    if (!conversationId.value) { conversationId.value = newId(); creationMonth = initialMonth.value }
    const message = { id: pending.value.id, text: pending.value.text, ...(pending.value.suggestionId ? { suggestionId: pending.value.suggestionId } : {}) }
    try {
      if (!created) {
        const raw = await ledger.chatRequest<unknown>(path('/conversations'), { method: 'POST', data: { id: conversationId.value, bookId: bookId.value, ...(protocol.value === 'v2' ? { initialMonth: creationMonth } : { month: creationMonth }) } })
        if (!current(version)) return false
        const result = protocol.value === 'v2' ? parseSummaryV2(raw) : raw as ConversationSummary
        if (result.id !== conversationId.value || result.bookId !== bookId.value) throw new Error('会话创建范围不一致，请重新读取')
        created = true
      }
      if (!current(version)) return false
      const endpoint = `/conversations/${encodeURIComponent(conversationId.value)}/messages/stream`
      const task = protocol.value === 'v2' ? ledger.streamChatV2(endpoint, message, event => {
        if (!current(version)) return
        if (event.event === 'status') progress.value = event.data.text
        if (event.event === 'snapshot') preview.value = event.data
        if (event.event === 'delta') {
          if (!preview.value || !('schemaVersion' in preview.value)) throw new Error('正文缺少完整预览，请重新读取')
          if (preview.value.answer.length + event.data.text.length > 2500) throw new Error('回答正文过长，请重新读取')
          preview.value = { ...preview.value, answer: preview.value.answer + event.data.text }
        }
      }) : ledger.streamChat(endpoint, message, event => {
        if (!current(version)) return
        if (event.event === 'status') progress.value = event.data.text
        if (event.event === 'snapshot') preview.value = event.data
        if (event.event === 'delta') { preview.value ||= { answer: '' }; preview.value.answer += event.data.text }
      })
      cancelStream = task.cancel
      const result = await task.promise
      if (!current(version)) return false
      if (result.id !== message.id || result.text !== message.text) throw new Error('回答与原消息不一致，请重新读取')
      turns.value = merge(turns.value, [result])
      if (isV2Turn(result)) contextV2.value = result.context
      pending.value = undefined; preview.value = undefined
      if (protocol.value === 'v2') {
        // done 可能是旧消息的幂等重放；再次读取顶部权威状态，避免覆盖其他设备的新筛选。
        try {
          const latest = parseConversationV2(await ledger.chatRequest<unknown>(path(`/conversations/${encodeURIComponent(conversationId.value)}`)))
          if (!current(version)) return false
          if (latest.id !== conversationId.value || latest.bookId !== bookId.value) throw new Error('当前会话范围不一致，请重新读取')
          const previousFirst = turns.value[0]?.sequence, latestFirst = latest.turns[0]?.sequence
          const prior = latestFirst ? turns.value.filter(turn => turn.sequence < latestFirst) : []
          const gap = !!latestFirst && prior.length > 0 && prior[prior.length - 1].sequence < latestFirst - 1
          turns.value = gap ? latest.turns : merge(turns.value, latest.turns)
          if (latest.updatedAt >= updatedAt) { contextV2.value = latest.context; updatedAt = latest.updatedAt }
          if (gap || !previousFirst || previousFirst >= (latestFirst || Infinity)) hasMore.value = latest.hasMore
        } catch (cause) { if (current(version)) { ready.value = false; report(cause) } }
      }
      if (!current(version)) return false
      await refreshHistory()
      return true
    } catch (cause) { if (current(version)) report(cause); return false }
    finally { if (current(version)) { sending.value = false; progress.value = ''; cancelStream = undefined } }
  }
  function retry(): Promise<boolean> { return pending.value ? send(pending.value.text, pending.value.suggestionId) : Promise.resolve(false) }
  function sendSuggestion(suggestion: SuggestionV2): Promise<boolean> {
    const source = turns.value.find(turn => isV2Turn(turn) && turn.id === suggestion.sourceTurnId)
    const stored = source && isV2Turn(source) && source.completeness === 'complete' ? source.suggestions.find(item => item.id === suggestion.id && item.text === suggestion.text) : undefined
    if (!stored) { error.value = '建议尚未保存或不属于当前对话，请重新读取'; return Promise.resolve(false) }
    return send(stored.text, stored.id)
  }
  return { turns, history, legacyHistory, legacyReadonly, conversationId, initialMonth, bookId, ownerId, protocol, ready, context, contextV2, suggestions, loading, sending, busy, error, pending, preview, progress, hasMore, clear, suspend, load, read, readLegacy, refreshHistory, refreshLegacyHistory, startFromLegacy, loadEarlier, send, retry, sendSuggestion, startNew }
})
