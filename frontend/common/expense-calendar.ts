import type { Entry, EntryType } from './types'
import { localDate } from './format'

export type CalendarMode = 'all' | EntryType

export interface ExpenseCalendarDay {
  date: string
  day: number
  inMonth: boolean
  isToday: boolean
  expense: number
  income: number
  expenseCount: number
  incomeCount: number
  level: number
  incomeLevel: number
}

export function isCalendarMonth(month: string): boolean {
  return /^(20\d{2}|2100)-(0[1-9]|1[0-2])$/.test(month)
}

export function isCalendarDate(date: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !isCalendarMonth(date.slice(0, 7))) return false
  const [year, month, day] = date.split('-').map(Number)
  return day >= 1 && day <= new Date(year, month, 0).getDate()
}

export function selectedDateInMonth(month: string, previous: string, today = localDate()): string {
  if (!isCalendarMonth(month)) throw new Error('请选择2000年至2100年之间的月份')
  if (isCalendarDate(previous) && previous.startsWith(month)) return previous
  if (today.startsWith(month)) return today
  const [year, value] = month.split('-').map(Number)
  const day = Math.min(Number(previous.slice(8)) || 1, new Date(year, value, 0).getDate())
  return `${month}-${String(day).padStart(2, '0')}`
}

// 账单金额始终按整数分累计；日历只缩写显示，明细保留精确金额。
export function calendarAmount(cents: number): string {
  if (!cents) return '—'
  if (cents >= 10000000000) return `${Number((cents / 10000000000).toFixed(1))}亿`
  if (cents >= 1000000) return `${Number((cents / 1000000).toFixed(1))}万`
  return String(Number((cents / 100).toFixed(2)))
}

export function filterCalendarEntries(entries: readonly Entry[], bookId: string, date: string, mode: CalendarMode): Entry[] {
  return entries.filter(entry => entry.bookId === bookId && entry.date === date && (mode === 'all' || entry.type === mode))
}

export function buildExpenseCalendar(month: string, entries: readonly Entry[], bookId: string, today = localDate()) {
  if (!isCalendarMonth(month)) throw new Error('请选择2000年至2100年之间的月份')
  const [year, value] = month.split('-').map(Number)
  const offset = (new Date(year, value - 1, 1).getDay() + 6) % 7
  const totals = new Map<string, { expense: number; income: number; expenseCount: number; incomeCount: number }>()
  entries.forEach(entry => {
    if (entry.bookId !== bookId || !entry.date.startsWith(month) || !isCalendarDate(entry.date)) return
    const total = totals.get(entry.date) || { expense: 0, income: 0, expenseCount: 0, incomeCount: 0 }
    if (entry.type === 'expense') { total.expense += entry.amount; total.expenseCount++ }
    else { total.income += entry.amount; total.incomeCount++ }
    totals.set(entry.date, total)
  })
  const maximum = Math.max(0, ...[...totals.values()].map(total => total.expense))
  const maximumIncome = Math.max(0, ...[...totals.values()].map(total => total.income))
  const days: ExpenseCalendarDay[] = Array.from({ length: 42 }, (_, index) => {
    const date = localDate(new Date(year, value - 1, index - offset + 1, 12))
    const total = totals.get(date) || { expense: 0, income: 0, expenseCount: 0, incomeCount: 0 }
    return { date, day: Number(date.slice(8)), inMonth: date.startsWith(month), isToday: date === today, ...total, level: total.expense ? Math.min(3, Math.ceil(total.expense / maximum * 3)) : 0, incomeLevel: total.income ? Math.min(3, Math.ceil(total.income / maximumIncome * 3)) : 0 }
  })
  const monthDays = days.filter(day => day.inMonth)
  return {
    days,
    expense: monthDays.reduce((sum, day) => sum + day.expense, 0),
    income: monthDays.reduce((sum, day) => sum + day.income, 0),
    expenseDays: monthDays.filter(day => day.expenseCount > 0).length,
    incomeDays: monthDays.filter(day => day.incomeCount > 0).length,
    peakDay: monthDays.find(day => day.expense > 0 && day.expense === maximum),
    peakIncomeDay: monthDays.find(day => day.income > 0 && day.income === maximumIncome),
  }
}
