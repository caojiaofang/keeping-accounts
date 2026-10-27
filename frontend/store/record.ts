import { computed, ref, watch } from 'vue'
import { defineStore } from 'pinia'
import { useLedgerStore } from '@/store/ledger'
import { createExampleDrafts } from '@/common/mock/assistant'
import type { AssistantDraft } from '@/common/mock/assistant'
import type { RecordHistory, RecordImage, RecordInput, RecordMessage, RecordResult } from '@/common/types/record'
import { newId } from '@/common/utils/id'

// 记账上下文只在当前页面保留，图片和聊天不写入本机缓存。
export const useRecordStore = defineStore('record', () => {
  const ledger = useLedgerStore()
  const messages = ref<RecordMessage[]>([])
  const history = ref<RecordHistory[]>([])
  const currentId = ref('')
  const sending = ref(false)
  const saving = ref(false)
  const error = ref('')
  const uncertainKeys = ref<string[]>([])
  const retry = ref<{ text: string; image?: RecordImage; messageId: string }>()
  let unresolvedImage: RecordImage | undefined
  let revision = 0
  const drafts = computed(() => messages.value.find(message => message.id === currentId.value)?.drafts || [])
  const pending = computed(() => drafts.value.filter(draft => draft.status === 'pending'))
  const busy = computed(() => sending.value || saving.value)
  const blocked = computed(() => busy.value || uncertainKeys.value.length > 0)

  function clear() {
    revision++
    messages.value = []
    history.value = []
    currentId.value = ''
    error.value = ''
    retry.value = undefined
    unresolvedImage = undefined
    uncertainKeys.value = []
    sending.value = false
    saving.value = false
  }

  watch(() => [ledger.accountId, ledger.activeBookId], clear, { flush: 'sync' })

  function finishContext() {
    // 完成的账单不再进入下一轮上下文，避免“再记一笔”重复生成旧账单。
    if (!pending.value.length) {
      history.value = []
      unresolvedImage = undefined
    }
  }

  async function send(text: string, image?: RecordImage, retryMessageId?: string): Promise<boolean> {
    text = text.trim()
    if (blocked.value || (!text && !image) || text.length > 200) return false
    if (messages.value.length >= 60 && !retryMessageId) {
      error.value = '本次对话较长，请先处理草稿，再点击“新对话”。'
      return false
    }
    const version = revision
    const messageId = retryMessageId || newId()
    if (!retryMessageId) messages.value.push({ id: messageId, role: 'user', text, imagePath: image?.path })
    const userMessage = messages.value.find(message => message.id === messageId)!
    userMessage.failed = false
    sending.value = true
    error.value = ''
    retry.value = undefined
    const effectiveImage = image || unresolvedImage
    const input: RecordInput = {
      text,
      history: history.value.map(turn => ({ ...turn })),
      drafts: pending.value.map(({ key, bookId, type, amount, categoryId, date, time, note, account, accountDefaulted }) => ({ key, bookId, type, amount, categoryId, date, time, note, account, accountDefaulted })),
      ...(effectiveImage ? { image: effectiveImage.dataUrl } : {}),
    }
    try {
      let result: RecordResult
      if (ledger.accountId) {
        result = await ledger.recordDrafts(input)
      } else {
        const example = !image && !pending.value.length ? createExampleDrafts(text, ledger.activeBookId) : null
        if (!example) throw new Error('体验模式支持下方预置示例，登录后可连续聊天和识别图片。')
        result = { drafts: example, reply: '已整理好示例账单，核对后可以确认记账。' }
      }
      if (version !== revision) return false
      if (!result.clarification) {
        const previous = messages.value.find(message => message.id === currentId.value)
        if (previous && pending.value.length) previous.superseded = true
      }
      const answer: RecordMessage = { id: newId(), role: 'assistant', text: result.reply }
      if (!result.clarification) {
        answer.drafts = result.drafts
        currentId.value = answer.id
      }
      messages.value.push(answer)
      history.value = [...history.value, { role: 'user' as const, text: text || '请识别这张账单图片' }, { role: 'assistant' as const, text: result.reply }].slice(-6)
      unresolvedImage = result.clarification ? effectiveImage : undefined
      if (!result.clarification && !result.drafts.length) finishContext()
      return true
    } catch (cause) {
      if (version === revision) {
        userMessage.failed = true
        error.value = cause instanceof Error ? cause.message : '识别失败，请重试本条消息'
        retry.value = { text, image, messageId }
      }
      return false
    } finally { if (version === revision) sending.value = false }
  }

  function retryLast() {
    const last = retry.value
    return last ? send(last.text, last.image, last.messageId) : Promise.resolve(false)
  }

  function editDraft(key: string, values: AssistantDraft) {
    if (blocked.value) return
    const draft = pending.value.find(item => item.key === key)
    if (!draft) return
    Object.assign(draft, values, { key: draft.key, bookId: draft.bookId, status: 'pending', time: values.time || undefined })
  }

  function dismissDraft(key: string) {
    if (blocked.value) return
    const draft = pending.value.find(item => item.key === key)
    if (draft) draft.status = 'dismissed'
    history.value = []
    finishContext()
  }

  async function confirm(keys: string[]): Promise<number> {
    if (busy.value) return 0
    saving.value = true
    error.value = ''
    retry.value = undefined
    const version = revision
    let count = 0
    try {
      for (const key of keys) {
        const draft = pending.value.find(item => item.key === key)
        if (!draft) continue
        // 发起保存后锁定内容；响应丢失时必须使用同一 key 和原内容重试。
        if (!uncertainKeys.value.includes(key)) uncertainKeys.value.push(key)
        const { key: requestId, status, accountDefaulted, ...entry } = draft
        const saved = await ledger.saveEntry(entry, undefined, ledger.accountId ? requestId : undefined)
        if (version !== revision) return count
        if (!saved) {
          error.value = '这笔账单尚未确认保存。请重试保存，或刷新明细核对；核对前暂时不能修改草稿。'
          return count
        }
        draft.status = 'saved'
        history.value = []
        unresolvedImage = undefined
        uncertainKeys.value = uncertainKeys.value.filter(item => item !== key)
        count++
      }
      finishContext()
      return count
    } catch {
      if (version === revision) error.value = '暂时无法确认保存结果，请重试保存或查看明细核对。'
      return count
    } finally { if (version === revision) saving.value = false }
  }

  return { messages, drafts, pending, sending, saving, busy, blocked, error, retry, uncertainKeys, clear, send, retryLast, editDraft, dismissDraft, confirm }
})
