import assert from 'node:assert/strict'
import path from 'node:path'
import { moduleUrl, root } from './test-modules.mjs'
const { overviewGroups, monthlyDueDates, signedBudgetMoney } = await import(await moduleUrl(path.join(root, 'common/services/budget-overview.ts')))
const { createFamilyBudgetExample, budgetTotals } = await import(await moduleUrl(path.join(root, 'common/services/budget-demo.ts')))
let passed = 0
function check(name, fn) { fn(); passed++; console.log('通过：' + name) }
const plan = createFamilyBudgetExample()
const rows = plan.items.map(item => ({ itemId: item.id, groupId: item.groupId, name: item.name, kind: item.kind, inPlan: true, plannedAmount: item.monthlyAmount, expenseAmount: 0, remainingAmount: item.kind === 'expense' ? item.monthlyAmount : null, depositedAmount: 0, usedAmount: 0, availableAmount: item.kind === 'expense' ? null : 0, fundVersion: 0 }))
const execution = { budget: { plan, totals: budgetTotals(plan) }, items: rows }
check('分组只加叶子分配，生活费4040、总分配15600', () => {
  const groups = overviewGroups(execution)
  assert.equal(groups[3].planned, 404000); assert.equal(groups.reduce((n, g) => n + g.planned, 0), 1560000)
})
check('支出、存入、用款和累计资金分别展示，不重复计为消费', () => {
  const changed = structuredClone(execution); changed.items[3].expenseAmount = 610000; changed.items[3].depositedAmount = 52000; changed.items[3].usedAmount = 610000; changed.items[3].availableAmount = -558000
  assert.equal(overviewGroups(changed).reduce((n, g) => n + g.expense, 0), 610000)
  assert.equal(overviewGroups(changed)[1].planned, 52000)
})
check('已归档且当前分组不存在的真实执行不会从列表消失', () => {
  const changed = structuredClone(execution)
  changed.items.push({ ...rows[5], itemId: 'archived', groupId: 'old', inPlan: false, plannedAmount: 0, expenseAmount: 5000000 })
  const last = overviewGroups(changed).at(-1)
  assert.equal(last.name, '本月计划外用途'); assert.equal(last.expense, 5000000); assert.equal(last.planned, 0)
})
check('未设置月份不套用当前方案，但保留历史用途的实际支出', () => {
  const groups = overviewGroups({ budget: { plan: null }, items: [{ ...rows[0], inPlan: false, plannedAmount: 0, expenseAmount: 10000 }] })
  assert.equal(groups.length, 1); assert.equal(groups[0].expense, 10000); assert.equal(monthlyDueDates(null, '2026-11').length, 0)
})
check('负的剩余额度和可用资金带负号，不能因金额格式化变成正数', () => {
  assert.equal(signedBudgetMoney(-20000), '−200.00'); assert.equal(signedBudgetMoney(0), '0.00'); assert.equal(signedBudgetMoney(40000), '400.00')
})
check('月末缴费按原日期回归，闰年二月与三月不漂移', () => {
  const p = structuredClone(plan); p.items = [{ ...p.items[0], dueDay: 31 }, { ...p.items[3], reserve: { expectedAmount: 610000, anchorDate: '2024-01-31', cycleMonths: 1 } }]
  assert.deepEqual(monthlyDueDates(p, '2024-02').map(v => v.date), ['2024-02-29', '2024-02-29'])
  assert.deepEqual(monthlyDueDates(p, '2024-03').map(v => v.date), ['2024-03-31', '2024-03-31'])
})
check('半年和年度只列应缴月份，启用锚点之前不虚构缴费', () => {
  const p = structuredClone(plan); p.items = [{ ...p.items[3], reserve: { expectedAmount: 610000, anchorDate: '2026-11-30', cycleMonths: 6 } }]
  assert.equal(monthlyDueDates(p, '2026-05').length, 0); assert.equal(monthlyDueDates(p, '2026-12').length, 0)
  assert.equal(monthlyDueDates(p, '2027-05')[0].date, '2027-05-30'); p.items[0].reserve.cycleMonths = 12
  assert.equal(monthlyDueDates(p, '2027-05').length, 0); assert.equal(monthlyDueDates(p, '2027-11')[0].amount, 610000)
})
console.log(`预算总览检查完成：${passed} 项通过。`)
