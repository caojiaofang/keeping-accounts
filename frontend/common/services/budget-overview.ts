import type { BudgetMonthExecution, BudgetItemExecution, BudgetPlan } from '../types/budget'
import { money } from '../utils/format'

export function signedBudgetMoney(value: number): string { return (value < 0 ? '−' : '') + money(value) }
export function overviewGroups(execution: BudgetMonthExecution) {
  const groups = execution.budget.plan?.groups || []
  const known = new Set(groups.map(g => g.id))
  const make = (id: string, name: string, items: BudgetItemExecution[]) => ({ id, name, items, planned: items.reduce((n, i) => n + i.plannedAmount, 0), expense: items.reduce((n, i) => n + i.expenseAmount, 0) })
  const result = groups.map(g => make(g.id, g.name, execution.items.filter(i => i.groupId === g.id)))
  const historical = execution.items.filter(i => !known.has(i.groupId))
  if (historical.length) result.push(make('historical', '本月计划外用途', historical))
  return result.filter(g => g.items.length)
}
/** 按原约定日算本月到期日；跨月不沿用上期被截短的月末日期。 */
export function monthlyDueDates(plan: BudgetPlan | null, month: string) {
  if (!plan) return []
  const [year, value] = month.split('-').map(Number)
  const lastDay = new Date(Date.UTC(year, value, 0)).getUTCDate()
  return plan.items.flatMap(item => {
    let day: number | undefined
    if (item.kind === 'expense' && item.dueDay) day = Math.min(item.dueDay, lastDay)
    if (item.reserve) {
      const [anchorYear, anchorMonth, anchorDay] = item.reserve.anchorDate.split('-').map(Number)
      const elapsed = (year - anchorYear) * 12 + value - anchorMonth
      if (elapsed >= 0 && elapsed % item.reserve.cycleMonths === 0) day = Math.min(anchorDay, lastDay)
    }
    return day ? [{ itemId: item.id, name: item.name, date: `${month}-${String(day).padStart(2, '0')}`, amount: item.reserve?.expectedAmount ?? item.monthlyAmount }] : []
  }).sort((a, b) => a.date.localeCompare(b.date) || a.name.localeCompare(b.name))
}
