import { ref } from 'vue'
import { defineStore } from 'pinia'
import { API_BASE_URL, ApiError } from '@/common/api/request'
import { createBudgetApi } from '@/common/api/budget'
import type { BudgetApi } from '@/common/api/budget'
import { createBudgetDemo } from '@/common/services/budget-demo'
import type * as B from '@/common/types/budget'
import type { Book, Entry } from '@/common/types'
import { canManageBook, canEditEntry } from '@/common/services/shared-books'
import { newId } from '@/common/utils/id'

type WriteMethod = 'savePlan' | 'confirm' | 'updateMonth' | 'equalize' | 'fund' | 'correctFund' | 'createGoal' | 'updateGoal' | 'setLink' | 'removeLink'
type ReadMethod = Exclude<keyof BudgetApi, WriteMethod>
interface Context { accountId: string | null; token: string; book: Book; month: string; ready: boolean }
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value))

export const useBudgetStore = defineStore('budget', () => {
  const plan = ref<B.BudgetPlanResponse | null>(null), month = ref<B.BudgetMonthResponse | null>(null)
  const execution = ref<B.BudgetMonthExecution | null>(null), detail = ref<B.BudgetExecutionDetail | null>(null), projects = ref<B.BudgetProjectPage | null>(null)
  const loading = ref(false), writing = ref(false), error = ref(''), refreshError = ref(''), conflict = ref(false)
  const notice = ref(''), retryAvailable = ref(false)
  const pendingMethod = ref<WriteMethod | null>(null)
  let context: Context | undefined, entries: () => Entry[] = () => [], epoch = 0, reads = 0, readRevision = 0, detailRevision = 0
  let source: BudgetApi | undefined
  let connectedKey = ''
  let pending: { method: WriteMethod; args: unknown[]; key: string } | undefined
  const demoSources = new Map<string, BudgetApi>()
  function clear() { plan.value = null; month.value = null; execution.value = null; detail.value = null; projects.value = null }
  function invalidate() { epoch++; readRevision++; detailRevision++; reads = 0; loading.value = false; writing.value = false; clear() }
  function scopeKey(c: Context) { return JSON.stringify([API_BASE_URL, c.accountId, c.token, c.book, c.month, c.ready]) }
  function connect(next: Context, getEntries: () => Entry[]) {
    entries = getEntries
    const nextKey = scopeKey(next)
    if (context && connectedKey === nextKey) { context = next; return }
    invalidate(); writing.value = false; pending = undefined; pendingMethod.value = null; retryAvailable.value = false
    error.value = ''; refreshError.value = ''; notice.value = ''; conflict.value = false
    context = next
    connectedKey = nextKey
    if (next.accountId) source = next.token && next.ready ? createBudgetApi(next.token, next.book.id) : undefined
    else {
      const key = `moji-budget-demo-v1:${next.book.id}`
      if (!demoSources.has(key)) {
        let saved: unknown
        try { saved = uni.getStorageSync(key) } catch { /* 无缓存时从空体验预算开始。 */ }
        demoSources.set(key, createBudgetDemo(next.book.id, () => entries(), value => uni.setStorageSync(key, clone(value)), saved))
      }
      source = demoSources.get(key)
    }
  }
  async function query<K extends ReadMethod>(method: K, ...args: Parameters<BudgetApi[K]>): Promise<Awaited<ReturnType<BudgetApi[K]>> | undefined> {
    if (!source) { error.value = '请先连接云端账本'; return undefined }
    const generation = epoch, api = source
    reads++; loading.value = true
    try {
      const result = await (api[method] as (...values: unknown[]) => Promise<unknown>)(...args)
      if (['plan', 'month', 'execution', 'projects', 'detail', 'link', 'entries'].includes(method)) {
        const value = result as { schemaVersion?: number; bookId?: string; month?: string }
        if (!value || value.schemaVersion !== 1 || value.bookId !== context?.book.id || (['month', 'execution'].includes(method) && value.month !== args[0])) { if (generation === epoch) clear(); throw new Error('预算响应归属不正确，请重新读取') }
      }
      return generation === epoch ? result as Awaited<ReturnType<BudgetApi[K]>> : undefined
    } catch (cause) {
      if (generation === epoch) { if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) clear(); error.value = cause instanceof Error ? cause.message : '预算读取失败，请重试' }
      return undefined
    } finally { if (generation === epoch) { reads--; loading.value = reads > 0 } }
  }
  async function load() {
    const c = context
    if (!c) return false
    const generation = epoch, revision = ++readRevision
    error.value = ''
    const values = await Promise.all([query('plan'), query('month', c.month), query('execution', c.month), query('projects')])
    if (generation !== epoch || revision !== readRevision || values.some(v => v === undefined)) return false
    ;[plan.value, month.value, execution.value, projects.value] = values as [B.BudgetPlanResponse, B.BudgetMonthResponse, B.BudgetMonthExecution, B.BudgetProjectPage]
    conflict.value = false; refreshError.value = ''
    return true
  }
  async function loadDetail(itemId: string, beforeRecordId?: string, beforeEntryId?: string) {
    const revision = ++detailRevision
    const value = await query('detail', itemId, beforeRecordId, beforeEntryId)
    if (value && revision === detailRevision) detail.value = value
    return value
  }
  async function execute(method: WriteMethod, args: unknown[]) {
    if (!context || !source || writing.value) return undefined
    const isLink = method === 'setLink' || method === 'removeLink'
    const entry = isLink ? entries().find(e => e.id === args[0] && e.bookId === context!.book.id) : undefined
    if (isLink ? !entry || !canEditEntry(context.book, entry) : !canManageBook(context.book)) { error.value = isLink ? '只能关联有权编辑的账单' : '只有账本创建者可以修改预算'; return undefined }
    const c = context, generation = ++epoch, key = scopeKey(c), api = source, item = detail.value?.item.id
    reads = 0; loading.value = false; readRevision++
    pending = { method, args: clone(args), key }; pendingMethod.value = method; retryAvailable.value = false
    writing.value = true; error.value = ''; conflict.value = false; refreshError.value = ''
    try {
      const result = await (api[method] as (...values: unknown[]) => Promise<unknown>)(...clone(args))
      if (generation !== epoch || !context || key !== scopeKey(context)) return undefined
      pending = undefined; pendingMethod.value = null; clear()
      await load()
      if (item && generation === epoch) await loadDetail(item)
      if (generation !== epoch) return undefined
      if (error.value) { refreshError.value = '已保存，最新预算读取失败，请刷新'; error.value = '' }
      return result
    } catch (cause) {
      if (generation === epoch) {
        error.value = cause instanceof Error ? cause.message : '保存失败，请重试'
        const status = cause instanceof ApiError ? cause.status : 0
        retryAvailable.value = status === 0 || status >= 500
        if (!retryAvailable.value) { pending = undefined; pendingMethod.value = null }
        if ([401, 403, 404, 409].includes(status)) clear()
        conflict.value = status === 409
      }
      return undefined
    } finally { if (generation === epoch) writing.value = false }
  }
  function write<K extends WriteMethod>(method: K, ...args: Parameters<BudgetApi[K]>) {
    if (retryAvailable.value) return Promise.resolve(undefined)
    return execute(method, args)
  }
  function retry() { return pending && context && pending.key === scopeKey(context) ? execute(pending.method, pending.args) : Promise.resolve(undefined) }
  function ledgerChanged(message = '') {
    const wasLoaded = !!month.value || !!detail.value, item = detail.value?.item.id
    notice.value = message
    // 账单更新只使视图过期，不能丢弃在途保存或解除写入锁；保存完成后会统一重读。
    if (writing.value) { readRevision++; detailRevision++; clear(); return }
    invalidate()
    if (wasLoaded && !writing.value) void load().then(() => item ? loadDetail(item) : undefined)
  }
  function resetDemo() { demoSources.clear(); if (context && !context.accountId) { uni.removeStorageSync(`moji-budget-demo-v1:${context.book.id}`); const c = context; context = undefined; connect(c, entries) } }
  return { plan, month, execution, detail, projects, loading, writing, error, refreshError, conflict, notice, retryAvailable, pendingMethod, connect, invalidate, load, loadDetail, query, write, retry, ledgerChanged, resetDemo, newRequestId: newId }
})
