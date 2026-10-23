import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

const toUrl = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
const transpile = source => ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } }).outputText
const formatUrl = toUrl(transpile(await readFile(new URL('../common/format.ts', import.meta.url), 'utf8')))
const compiled = transpile(await readFile(new URL('../common/expense-calendar.ts', import.meta.url), 'utf8'))
const { buildExpenseCalendar, calendarAmount, selectedDateInMonth, isCalendarDate, isCalendarMonth } = await import(toUrl(compiled.replace(/from (['"])\.\/format\1/g, `from '${formatUrl}'`)))
const entry = (id, date, amount, type = 'expense', bookId = 'test') => ({ id, date, amount, type, bookId, categoryId: 'food', note: id, account: '微信', createdAt: 1 })
const today = '2026-10-23'
const records = [entry('早餐', today, 800), entry('午餐', today, 2550), entry('收入', today, 100000, 'income'), entry('昨天', '2026-10-22', 100), entry('上月', '2026-09-23', 99999), entry('其他账本', today, 900000, 'expense', 'other'), entry('无效日期', '2026-10-32', 50000)]
let count = 0
function check(name, run) { run(); count++; console.log(`通过：${name}`) }

check('六行七列，按周一开头，月首星期对齐', () => {
  const calendar = buildExpenseCalendar('2026-10', [], 'test', today)
  assert.equal(calendar.days.length, 42)
  assert.equal(calendar.days[0].date, '2026-09-28')
  assert.equal(calendar.days[3].date, '2026-10-01')
  assert.equal(calendar.days.filter(day => day.inMonth).length, 31)
})
check('周日月首保留六格前月日期', () => {
  const calendar = buildExpenseCalendar('2026-11', [], 'test', today)
  assert.equal(calendar.days[6].date, '2026-11-01')
  assert.equal(calendar.days[6].inMonth, true)
})
check('闰年二月与整世纪非闰年', () => {
  assert.equal(buildExpenseCalendar('2024-02', [], 'test', today).days.filter(day => day.inMonth).length, 29)
  assert.equal(buildExpenseCalendar('2100-02', [], 'test', today).days.filter(day => day.inMonth).length, 28)
})
check('单日金额以分精确累加，收入不抵减消费', () => {
  const calendar = buildExpenseCalendar('2026-10', records, 'test', today)
  const day = calendar.days.find(day => day.date === today)
  assert.equal(day.expense, 3350)
  assert.equal(day.income, 100000)
  assert.equal(day.expenseCount, 2)
  assert.equal(day.isToday, true)
  assert.equal(calendar.expense, 3450)
  assert.equal(calendar.expenseDays, 2)
  assert.equal(calendar.peakDay.date, today)
})
check('隔离账本、月份及无效日期，补位日期不带金额', () => {
  const calendar = buildExpenseCalendar('2026-10', records, 'other', today)
  assert.equal(calendar.expense, 900000)
  assert.ok(calendar.days.filter(day => !day.inMonth).every(day => day.expense === 0 && day.income === 0))
})
check('仅收入日期不算支出日，不产生消费热度', () => {
  const calendar = buildExpenseCalendar('2026-10', [entry('工资', today, 500000, 'income')], 'test', today)
  assert.equal(calendar.expense, 0)
  assert.equal(calendar.expenseDays, 0)
  assert.equal(calendar.peakDay, undefined)
  assert.equal(calendar.days.find(day => day.date === today).level, 0)
})
check('空月份不出现最高消费日期或非零热度', () => {
  const calendar = buildExpenseCalendar('2026-10', [], 'test', today)
  assert.equal(calendar.peakDay, undefined)
  assert.ok(calendar.days.every(day => day.level === 0))
})
check('日期切换保持月内有效，月末自动夹到目标月最后一天', () => {
  assert.equal(selectedDateInMonth('2025-02', '2025-01-31', today), '2025-02-28')
  assert.equal(selectedDateInMonth('2024-02', '2024-01-31', today), '2024-02-29')
  assert.equal(selectedDateInMonth('2026-10', '2026-09-30', today), today)
})
check('日期与月份校验拒绝越界和无效日期', () => {
  assert.equal(isCalendarDate('2026-02-29'), false)
  assert.equal(isCalendarDate('2024-02-29'), true)
  assert.equal(isCalendarDate('2026-10-00'), false)
  assert.equal(isCalendarMonth('1999-12'), false)
  assert.equal(isCalendarMonth('2101-01'), false)
})
check('金额缩写覆盖零、小数和大额', () => {
  assert.equal(calendarAmount(0), '—')
  assert.equal(calendarAmount(800), '8')
  assert.equal(calendarAmount(3590), '35.9')
  assert.equal(calendarAmount(999999), '9999.99')
  assert.equal(calendarAmount(1234500), '1.2万')
  assert.equal(calendarAmount(20000000000), '2亿')
})
console.log(`消费日历检查完成：${count}项通过。`)
