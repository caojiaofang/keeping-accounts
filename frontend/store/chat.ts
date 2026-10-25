import { computed, ref, watch } from 'vue'
import { defineStore } from 'pinia'
import { useLedgerStore } from '@/store/ledger'
import { newId } from '@/common/utils/id'
import type { ChatTurn, Conversation, ConversationSummary } from '@/common/types/chat'

export const useChatStore = defineStore('chat', () => {
  const ledger = useLedgerStore()
  const turns = ref<ChatTurn[]>([])
  const history = ref<ConversationSummary[]>([])
  const conversationId = ref('')
  const loading = ref(false)
  const sending = ref(false)
  const error = ref('')
  const pending = ref<{ id: string; text: string }>()
  const preview = ref<Pick<ChatTurn, 'answer' | 'result'>>()
  const progress = ref('')
  let cancelStream: (() => void) | undefined
  const hasMore = ref(false)
  const initialMonth = ref('')
  const bookId = ref('')
  const ownerId = ref<string | null>(null)
  const context = computed(() => turns.value[turns.value.length - 1]?.context)
  const busy = computed(() => loading.value || sending.value)
  let generation = 0
  watch(() => ledger.accountId, clear, { flush: 'sync' })
  watch(() => ledger.activeBookId, clear, { flush: 'sync' })

  function clear(): void {
    generation++
    cancelStream?.()
    cancelStream = undefined
    preview.value = undefined
    progress.value = ''
    turns.value = []
    history.value = []
    conversationId.value = ''
    loading.value = false
    sending.value = false
    pending.value = undefined
    error.value = ''
    hasMore.value = false
    bookId.value = ''
    initialMonth.value = ''
    ownerId.value = null
  }

  function current(version: number): boolean {
    return version === generation && ownerId.value === ledger.accountId && bookId.value === ledger.activeBookId
  }

  function report(cause: unknown): void { error.value = cause instanceof Error ? cause.message : '对话暂时不可用，请重试' }

  async function refreshHistory(): Promise<boolean> {
    const version = generation
    try {
      const result = await ledger.chatRequest<ConversationSummary[]>(`/conversations?bookId=${encodeURIComponent(bookId.value)}&month=${initialMonth.value}`)
      if (!current(version)) return false
      history.value = result
      return true
    } catch (cause) { if (current(version)) report(cause); return false }
  }

  async function read(id: string): Promise<boolean> {
    if (busy.value) return false
    const version = ++generation
    loading.value = true
    error.value = ''
    try {
      const result = await ledger.chatRequest<Conversation>(`/conversations/${id}`)
      if (!current(version)) return false
      if (result.bookId !== bookId.value || result.month !== initialMonth.value) throw new Error('会话范围不一致，请重新选择')
      turns.value = result.turns
      conversationId.value = id
      hasMore.value = result.hasMore
      pending.value = undefined
      preview.value = undefined
      return true
    } catch (cause) { if (current(version)) report(cause); return false }
    finally { if (current(version)) loading.value = false }
  }

  async function load(month: string): Promise<void> {
    clear()
    if (!ledger.accountId) return
    initialMonth.value = month
    bookId.value = ledger.activeBookId
    ownerId.value = ledger.accountId
    const version = generation
    loading.value = true
    const loaded = await refreshHistory()
    if (!current(version)) return
    loading.value = false
    if (loaded && history.value.length) await read(history.value[0].id)
  }

  function startNew(): void {
    if (busy.value) return
    generation++
    conversationId.value = ''
    turns.value = []
    pending.value = undefined
    preview.value = undefined
    progress.value = ''
    hasMore.value = false
    error.value = ''
  }

  async function loadEarlier(): Promise<void> {
    if (busy.value || !hasMore.value || !turns.value.length) return
    const version = generation
    loading.value = true
    try {
      const result = await ledger.chatRequest<Conversation>(`/conversations/${conversationId.value}?before=${turns.value[0].sequence}`)
      if (!current(version)) return
      turns.value = [...result.turns, ...turns.value]
      hasMore.value = result.hasMore
    } catch (cause) { if (current(version)) report(cause) }
    finally { if (current(version)) loading.value = false }
  }

  async function send(text: string): Promise<boolean> {
    const question = text.trim()
    if (!question || busy.value) return false
    if (pending.value && pending.value.text !== question) { error.value = '上一条消息尚未确认，请先重试或重新读取对话'; return false }
    if (!ownerId.value || !current(generation)) { error.value = '请先读取当前账号的对话'; return false }
    const version = generation
    sending.value = true
    error.value = ''
    preview.value = undefined
    progress.value = '正在连接分析服务…'
    pending.value ||= { id: newId(), text: question }
    conversationId.value ||= newId()
    try {
      await ledger.chatRequest<ConversationSummary>('/conversations', { method: 'POST', data: { id: conversationId.value, bookId: bookId.value, month: initialMonth.value } })
      if (!current(version)) return false
      const task = ledger.streamChat(`/conversations/${conversationId.value}/messages/stream`, { ...pending.value }, event => {
        if (!current(version)) return
        if (event.event === 'status') progress.value = event.data.text
        if (event.event === 'snapshot') preview.value = event.data
        if (event.event === 'delta') {
          preview.value ||= { answer: '' }
          preview.value.answer += event.data.text
        }
      })
      cancelStream = task.cancel
      const result = await task.promise
      if (!current(version)) return false
      turns.value = [...turns.value.filter(turn => turn.id !== result.id), result].sort((a, b) => a.sequence - b.sequence)
      pending.value = undefined
      preview.value = undefined
      await refreshHistory()
      return true
    } catch (cause) { if (current(version)) report(cause); return false }
    finally { if (current(version)) { sending.value = false; progress.value = ''; cancelStream = undefined } }
  }

  return { turns, history, conversationId, initialMonth, bookId, ownerId, context, loading, sending, busy, error, pending, preview, progress, hasMore, clear, load, read, refreshHistory, loadEarlier, send, startNew }
})
