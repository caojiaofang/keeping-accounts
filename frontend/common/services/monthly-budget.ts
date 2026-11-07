import type { BudgetMonthExecution } from '../types/budget'

export function previousBudgetMonth(month: string): string | null {
  const [year, value] = month.split('-').map(Number)
  if (year === 2000 && value === 1) return null
  return `${value === 1 ? year - 1 : year}-${String(value === 1 ? 12 : value - 1).padStart(2, '0')}`
}

/** 总支出包含未关联账单，避免只关联部分消费时低估本月超支。 */
export function monthlyBudgetUsage(view: BudgetMonthExecution) {
  const amount = view.budget.totals?.expenseAmount || 0
  return { amount, spent: view.expenseTotal, remaining: Math.max(amount - view.expenseTotal, 0), over: Math.max(view.expenseTotal - amount, 0) }
}
