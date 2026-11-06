import assert from 'node:assert/strict'
import path from 'node:path'
import { moduleUrl, root } from './test-modules.mjs'
const { cents, toBudgetForm, fromBudgetForm, singleMonthOverride, sourcePlan, periodProjection } = await import(await moduleUrl(path.join(root, 'common/services/budget-editor.ts')))
const { createFamilyBudgetExample, budgetTotals } = await import(await moduleUrl(path.join(root, 'common/services/budget-demo.ts')))
let passed = 0
function check(name, fn) { fn(); passed++; console.log('通过：' + name) }
check('完整模板可编辑回传，15600 与4040不重复计入', () => {
  const plan = createFamilyBudgetExample(), result = fromBudgetForm(toBudgetForm(plan))
  assert.deepEqual(result, plan); assert.equal(budgetTotals(result).totalAllocated, 1560000); assert.equal(budgetTotals(result).groupTotals[3].amount, 404000)
})
check('小数金额精确转换，0有效，字符串和负数不能伪造资金', () => {
  assert.equal(cents('0.29'), 29); assert.equal(cents('0'), 0); assert.equal(cents('9999999.99'), 999999999)
  for (const value of ['-1', '1e3', '.1', '12.345', '', 'NaN', '10000000', '0x10']) assert.throws(() => cents(value))
  assert.throws(() => cents('0', true))
})
check('年度预留520和储蓄4200保持用户选择，不强行凑成6100', () => {
  const result = fromBudgetForm(toBudgetForm(createFamilyBudgetExample()))
  assert.equal(result.items[3].monthlyAmount * 12, 624000); assert.equal(result.items[3].reserve.expectedAmount, 610000)
  assert.equal(result.items[5].monthlyAmount * 12, 5040000)
  assert.deepEqual(periodProjection('520', 12, '6100'), { planned: 624000, difference: 14000 })
  assert.deepEqual(periodProjection('500', 12, '6100'), { planned: 600000, difference: -10000 })
  assert.equal(periodProjection('4200', 6).planned, 2520000); assert.equal(periodProjection('4200', 12).planned, 5040000)
  assert.equal(periodProjection('9999999.99', 12).planned, 11999999988); assert.throws(() => periodProjection('坏金额', 12))
})
check('仅本月完整传金额含0，结构变化拒绝，父级没有第二份金额', () => {
  const base = createFamilyBudgetExample(), edited = structuredClone(base); edited.items[0].monthlyAmount = 0
  const value = singleMonthOverride(edited, base); assert.equal(value.itemAmounts[0].amount, 0); assert.equal(value.itemAmounts.length, 14)
  edited.items.pop(); assert.throws(() => singleMonthOverride(edited, base))
})
check('日期、名称、分组、缴费日和备注校验有明确错误', () => {
  for (const change of [f => f.groups[0].name = '', f => f.items[0].name = ' ', f => f.items[0].groupId = 'missing', f => f.items[0].dueDay = '32', f => f.items[3].anchor = '2026-02-30', f => f.items[0].note = '长'.repeat(81)]) {
    const form = toBudgetForm(createFamilyBudgetExample()); change(form); assert.throws(() => fromBudgetForm(form))
  }
})
check('未来版本按生效月选取，启用前为空，表单不会原地改写来源', () => {
  const plan = createFamilyBudgetExample(), future = structuredClone(plan); future.plannedIncome = 2000000
  const revisions = [{ id: 'b', effectiveMonth: '2027-01', plan: future }, { id: 'a', effectiveMonth: '2026-11', plan }]
  assert.equal(sourcePlan(revisions, '2026-10'), null); assert.equal(sourcePlan(revisions, '2026-12'), plan); assert.equal(sourcePlan(revisions, '2027-01'), future)
  const form = toBudgetForm(plan); form.items[0].name = '修改'; assert.equal(plan.items[0].name, '房贷')
})
console.log(`预算设置表单检查完成：${passed} 项通过。`)
