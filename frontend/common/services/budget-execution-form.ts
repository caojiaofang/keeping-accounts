import { cents, yuan } from './budget-editor'
import type { BudgetGoalResponse } from '../types/budget'

export function executionDate(value: string): string {
  if (!/^(20\d{2}|2100)-\d{2}-\d{2}$/.test(value)) throw new Error('日期须为 2000—2100 年的有效日期')
  const day = new Date(value + 'T00:00:00Z')
  if (!Number.isFinite(day.getTime()) || day.toISOString().slice(0, 10) !== value) throw new Error('请填写有效日期')
  return value
}
export function fundFields(amount: string, date: string, note: string, today: string) {
  const when = executionDate(date)
  if (when > today) throw new Error('实际资金日期不能晚于今天')
  if (note.length > 80) throw new Error('备注最多 80 个字')
  return { amount: cents(amount, true), date: when, note: note.trim() }
}
export function goalTerms(amount: string, start: string, end: string, carry: string) {
  const startDate = executionDate(start), targetDate = executionDate(end)
  if (startDate > targetDate) throw new Error('开始日不能晚于截止日')
  return { targetAmount: cents(amount, true), startDate, targetDate, carryInAmount: cents(carry) }
}
export function nextGoalDates(last: BudgetGoalResponse | undefined, today: string) {
  let start = today
  if (last && last.targetDate >= start) {
    const date = new Date(last.targetDate + 'T00:00:00Z'); date.setUTCDate(date.getUTCDate() + 1); start = date.toISOString().slice(0, 10)
  }
  executionDate(start)
  const [year, month, day] = start.split('-').map(Number)
  const targetMonth = new Date(Date.UTC(year, month - 1 + 6, 1))
  const maxDay = new Date(Date.UTC(targetMonth.getUTCFullYear(), targetMonth.getUTCMonth() + 1, 0)).getUTCDate()
  targetMonth.setUTCDate(Math.min(day, maxDay)); targetMonth.setUTCDate(targetMonth.getUTCDate() - 1)
  return { start, end: executionDate(targetMonth.toISOString().slice(0, 10)) }
}
export function defaultCarry(available: number) { return yuan(Math.min(Math.max(available, 0), 999999999)) }
