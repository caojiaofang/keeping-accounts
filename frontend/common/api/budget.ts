/** 独立预算 API；调用方固定请求 ID，失败重试不得改写正文。 */
import { apiRequest } from './request'
import type * as B from '../types/budget'

export function createBudgetApi(token: string, bookId: string) {
  const prefix = `/api/v1/books/${encodeURIComponent(bookId)}/budgets`
  const call = <T>(path: string, method: 'GET' | 'POST' | 'PUT' | 'DELETE' = 'GET', body?: object) =>
    apiRequest<T>(prefix + path, { token, method, data: body as Record<string, unknown> | undefined })
  const id = encodeURIComponent
  const query = (values: Record<string, string | number | boolean | undefined>) => {
    const parts = Object.entries(values).filter(([, value]) => value !== undefined).map(([key, value]) => `${key}=${id(String(value))}`)
    return parts.length ? '?' + parts.join('&') : ''
  }
  return {
    plan: () => call<B.BudgetPlanResponse>('/plan'),
    previewPlan: (body: B.PreviewBudgetRevision) => call<B.BudgetImpactResponse>('/plan/preview', 'POST', body),
    savePlan: (body: B.SaveBudgetRevision) => call<B.SavedBudgetRevision>('/plan', 'PUT', body),
    month: (month: string) => call<B.BudgetMonthResponse>(`/months/${id(month)}`),
    monthly: (month: string) => call<B.BudgetMonthResponse>(`/monthly/${id(month)}`),
    summary: (month: string) => call<B.BudgetMonthSummary>(`/monthly/${id(month)}/summary`),
    saveMonthly: (month: string, body: B.SaveMonthlyBudget) => call<B.BudgetMonthResponse>(`/monthly/${id(month)}`, 'PUT', body),
    deleteMonthly: (month: string, body: B.DeleteMonthlyBudget) => call<B.BudgetMonthResponse>(`/monthly/${id(month)}`, 'DELETE', body),
    monthlyExecution: (month: string) => call<B.BudgetMonthExecution>(`/monthly/${id(month)}/execution`),
    confirm: (month: string, body: B.ConfirmBudgetMonth) => call<B.BudgetMonthResponse>(`/months/${id(month)}/confirm`, 'POST', body),
    updateMonth: (month: string, body: B.UpdateBudgetMonth) => call<B.BudgetMonthResponse>(`/months/${id(month)}`, 'PUT', body),
    previewEqualization: (body: B.PreviewBudgetEqualization) => call<B.BudgetEqualizationPreview>('/equalizations/preview', 'POST', body),
    equalize: (body: B.ApplyBudgetEqualization) => call<B.BudgetEqualizationResponse>('/equalizations', 'POST', body),
    projects: (after?: string) => call<B.BudgetProjectPage>('/items' + query({ after })),
    detail: (item: string, beforeRecordId?: string, beforeEntryId?: string) => call<B.BudgetExecutionDetail>(`/items/${id(item)}` + query({ beforeRecordId, beforeEntryId })),
    fund: (item: string, body: B.CreateBudgetFund) => call<B.SavedBudgetFund>(`/items/${id(item)}/funds`, 'POST', body),
    correctFund: (item: string, record: string, body: B.CorrectBudgetFund) => call<B.SavedBudgetFund>(`/items/${id(item)}/funds/${id(record)}/correct`, 'POST', body),
    createGoal: (item: string, body: B.CreateBudgetGoal) => call<B.SavedBudgetGoal>(`/items/${id(item)}/goals`, 'POST', body),
    updateGoal: (item: string, goal: string, body: B.UpdateBudgetGoal) => call<B.SavedBudgetGoal>(`/items/${id(item)}/goals/${id(goal)}`, 'PUT', body),
    fundingPreview: (item: string, goalId: string) => call<B.BudgetFundingPreview>(`/items/${id(item)}/funding-preview` + query({ goalId })),
    link: (entry: string) => call<B.BudgetEntryLinkState>(`/entries/${id(entry)}/link`),
    setLink: (entry: string, body: B.SetBudgetEntryLink) => call<B.BudgetEntryLinkState>(`/entries/${id(entry)}/link`, 'PUT', body),
    removeLink: (entry: string, body: B.RemoveBudgetEntryLink) => call<B.BudgetEntryLinkState>(`/entries/${id(entry)}/link`, 'DELETE', body),
    execution: (month: string) => call<B.BudgetMonthExecution>(`/months/${id(month)}/execution`),
    entries: (month: string, options: { itemId?: string; unlinkedOnly?: boolean; beforeEntryId?: string } = {}) => call<B.BudgetEntryPage>(`/months/${id(month)}/entries` + query(options)),
  }
}
export type BudgetApi = ReturnType<typeof createBudgetApi>
