import { ref } from 'vue'
import { defineStore } from 'pinia'
import { API_BASE_URL, ApiError } from '@/common/api/request'
import type { BudgetApi } from '@/common/api/budget'
import type { Book } from '@/common/types'
import type { BudgetMonthSummary } from '@/common/types/budget'

interface Context { accountId: string | null; token: string; book: Book; month: string; ready: boolean }
const FRESH_MS = 3000

/** 明细和「我的」共用短期内存结果；不持久化金额或凭据，不加载项目及资金明细。 */
export const useBudgetSummaryStore = defineStore('budget-summary', () => {
  const data = ref<BudgetMonthSummary | null>(null), loading = ref(false), error = ref(''), revision = ref(0)
  let context: Context | undefined, source: BudgetApi | undefined, key = '', generation = 0, fetchedAt = 0
  let task: Promise<boolean> | undefined
  function invalidate(clear = false) {
    generation++; task = undefined; loading.value = false; fetchedAt = 0; error.value = ''
    if (clear) data.value = null
    revision.value++
  }
  function connect(next: Context, api: BudgetApi | undefined) {
    const nextKey = JSON.stringify([API_BASE_URL, next.accountId, next.token, next.book.id, next.book.kind, next.book.currentUserId, next.book.members, next.month, next.ready])
    context = next; source = api
    if (nextKey === key) return
    key = nextKey; invalidate(true)
  }
  function load(force = false): Promise<boolean> {
    if (!context || !source) return Promise.resolve(false)
    if (task) return task
    if (!force && data.value && !error.value && Date.now() - fetchedAt < FRESH_MS) return Promise.resolve(true)
    const current = generation, c = context, api = source
    loading.value = true; error.value = ''
    let run!: Promise<boolean>
    run = (async () => {
      try {
        const value = await Promise.resolve().then(() => api.summary(c.month))
        if (current !== generation) return false
        const money = (v: unknown) => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0
        if (!value || value.schemaVersion !== 1 || value.bookId !== c.book.id || value.month !== c.month || !money(value.expenseAmount) || !money(value.version) || !money(value.generatedAt)
          || (value.state === 'confirmed' ? !money(value.budgetAmount) || value.remainingAmount !== Math.max(value.budgetAmount! - value.expenseAmount, 0) || value.overAmount !== Math.max(value.expenseAmount - value.budgetAmount!, 0)
            : value.state !== 'not_set' || value.budgetAmount !== null || value.remainingAmount !== null || value.overAmount !== null)) {
          data.value = null
          throw new Error('预算摘要响应不完整，请重试')
        }
        data.value = value; fetchedAt = Date.now(); return true
      } catch (cause) {
        if (current === generation) {
          if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) data.value = null
          error.value = cause instanceof Error ? cause.message : '预算摘要读取失败，请重试'
        }
        return false
      } finally { if (current === generation && task === run) { loading.value = false; task = undefined } }
    })()
    task = run
    return run
  }
  return { data, loading, error, revision, connect, load, invalidate }
})
