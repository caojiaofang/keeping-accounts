import assert from 'node:assert/strict'
import path from 'node:path'
import { moduleUrl, root } from './test-modules.mjs'
import { createPinia, setActivePinia } from 'pinia'
const { createBudgetDemo, createFamilyBudgetExample } = await import(await moduleUrl(path.join(root, 'common/services/budget-demo.ts')))
const { fundFields, goalTerms, nextGoalDates, defaultCarry } = await import(await moduleUrl(path.join(root, 'common/services/budget-execution-form.ts')))
const { useBudgetStore } = await import(await moduleUrl(path.join(root, 'store/budget.ts')))
const today = new Date(Date.now() + 28800000).toISOString().slice(0, 10), month = today.slice(0, 7)
let passed = 0, serial = 0
const req = () => 'execution-' + ++serial
async function check(name, fn) { await fn(); passed++; console.log('通过：' + name) }
await check('实际金额按整数分校验，未来或无效日期拒绝', () => {
  assert.equal(fundFields('4200.29', today, ' 实际 ', today).amount, 420029)
  for (const amount of ['0', '-1', '1e3', '4.201']) assert.throws(() => fundFields(amount, today, '', today))
  assert.throws(() => fundFields('10', '2026-02-30', '', today)); assert.throws(() => fundFields('10', '2100-12-31', '', today))
})
await check('目标日期与结转约束，负余额不能变成虚构结转', () => {
  assert.equal(goalTerms('50000', today, today, '400').carryInAmount, 40000)
  assert.throws(() => goalTerms('0', today, today, '0')); assert.throws(() => goalTerms('1', '2027-02-01', '2027-01-01', '0'))
  assert.equal(defaultCarry(-50000), '0.00'); assert.equal(defaultCarry(40000), '400.00')
})
await check('续期从历史截止后开始，月末跨年日期有效', () => {
  assert.equal(nextGoalDates({ targetDate: '2026-12-31' }, '2026-11-06').start, '2027-01-01')
  assert.equal(nextGoalDates({ targetDate: '2026-01-30' }, '2026-01-30').end, '2026-07-30')
  assert.throws(() => nextGoalDates({ targetDate: '2100-12-31' }, '2100-12-31'))
})
let bills = [], stored
const api = createBudgetDemo('execution', () => bills, value => { stored = structuredClone(value) })
const plan = createFamilyBudgetExample(), saving = plan.items[5].id, reserve = plan.items[3].id
const input = { version: 0, effectiveMonth: month, plan }
const preview = await api.previewPlan(input); await api.savePlan({ ...input, impactToken: preview.impactToken, requestId: req() })
await check('正常记账后明确关联，重复设置不重复扣款', async () => {
  await api.fund(reserve, { requestId: req(), fundVersion: 0, id: 'reserve', kind: 'deposit', amount: 52000, date: today })
  bills.push({ id: 'bill', bookId: 'execution', type: 'expense', amount: 61000, date: today, note: '实际缴费', version: 1 })
  assert.equal((await api.detail(reserve)).balance.availableAmount, 52000)
  await api.setLink('bill', { requestId: req(), entryVersion: 1, linkVersion: 0, itemId: reserve })
  const fv = (await api.detail(reserve)).item.fundVersion
  await api.setLink('bill', { requestId: req(), entryVersion: 1, linkVersion: 1, itemId: reserve })
  assert.equal((await api.detail(reserve)).item.fundVersion, fv); assert.equal((await api.detail(reserve)).balance.availableAmount, -9000)
  assert.equal((await api.execution(month)).expenseTotal, 61000)
})
await check('改账单后关联版本前进，取消保留墓碑，再关联不回到0', async () => {
  bills[0] = { ...bills[0], amount: 50000, version: 2 }
  const current = await api.link('bill'); assert.equal(current.linkVersion, 2); assert.equal((await api.detail(reserve)).balance.availableAmount, 2000)
  await assert.rejects(api.removeLink('bill', { requestId: req(), entryVersion: 1, linkVersion: 1 }))
  await api.removeLink('bill', { requestId: req(), entryVersion: 2, linkVersion: 2 })
  assert.equal((await api.link('bill')).linkVersion, 3); assert.equal((await api.detail(reserve)).balance.availableAmount, 52000)
  await api.setLink('bill', { requestId: req(), entryVersion: 2, linkVersion: 3, itemId: reserve })
  assert.equal((await api.link('bill')).linkVersion, 4)
  bills = []; assert.equal((await api.detail(reserve)).balance.availableAmount, 52000)
})
let goalId
await check('筹集50400、还贷50000后仍保留完成进度和400余款', async () => {
  await api.fund(saving, { requestId: req(), fundVersion: 0, id: 'opening', kind: 'opening', amount: 5040000, date: today })
  goalId = 'mortgage'
  await api.createGoal(saving, { requestId: req(), fundVersion: 1, id: goalId, startDate: today, targetDate: today, targetAmount: 5000000, carryInAmount: 5040000 })
  bills.push({ id: 'mortgage-bill', bookId: 'execution', type: 'expense', amount: 5000000, date: today, note: '提前还贷', version: 1 })
  await api.setLink('mortgage-bill', { requestId: req(), entryVersion: 1, linkVersion: 0, itemId: saving, goalPeriodId: goalId })
  const detail = await api.detail(saving)
  assert.equal(detail.balance.availableAmount, 40000); assert.equal(detail.goals[0].collectedAmount, 5040000); assert.equal(detail.goals[0].usedAmount, 5000000); assert.equal(detail.goals[0].forecast.gapAmount, 0)
})
await check('明确关闭后续建，400结转不创造第二份钱，闭期实际修正仍有效', async () => {
  const d = await api.detail(saving), g = d.goals[0]
  await api.updateGoal(saving, goalId, { requestId: req(), fundVersion: d.item.fundVersion, version: g.version, startDate: g.startDate, targetDate: g.targetDate, targetAmount: g.targetAmount, carryInAmount: g.carryInAmount, state: 'closed' })
  const next = nextGoalDates(g, today)
  await api.createGoal(saving, { requestId: req(), fundVersion: d.item.fundVersion + 1, id: 'next-goal', startDate: next.start, targetDate: next.end, targetAmount: 2500000, carryInAmount: 40000 })
  assert.equal((await api.detail(saving)).balance.availableAmount, 40000)
  bills[0] = { ...bills[0], amount: 4950000, version: 2 }
  assert.equal((await api.detail(saving)).balance.availableAmount, 90000); assert.equal((await api.detail(saving)).goals[0].usedAmount, 4950000)
})
await check('修正期初保留替代链，原请求重试不重复资金', async () => {
  const d = await api.detail(saving), opening = d.records.find(r => r.id === 'opening')
  const body = { requestId: req(), fundVersion: d.item.fundVersion, recordVersion: opening.version, replacement: { id: 'opening-corrected', amount: 5050000, date: today } }
  const result = await api.correctFund(saving, opening.id, body)
  assert.deepEqual(await api.correctFund(saving, opening.id, body), result)
  assert.equal(result.replacement.replacesId, opening.id); assert.equal(result.balance.availableAmount, 100000)
  assert.ok(stored.records.some(r => r.id === opening.id && r.voidedAt !== null))
})
await check('共同成员能查看，但只能关联自己的账单且不能修改储备目标', async () => {
  const storage = new Map()
  globalThis.uni = { getStorageSync: k => storage.get(k), setStorageSync: (k, v) => storage.set(k, structuredClone(v)), removeStorageSync: k => storage.delete(k), request: () => assert.fail('体验不能联网') }
  setActivePinia(createPinia())
  const store = useBudgetStore(), book = { id: 'shared-test', kind: 'shared', currentUserId: 'demo-me', members: [{ userId: 'demo-me', role: 'owner', status: 'active' }, { userId: 'partner', role: 'member', status: 'active' }] }
  const entries = ['demo-me', 'partner'].map((createdBy, i) => ({ id: 'member-bill-' + i, bookId: book.id, createdBy, type: 'expense', date: today, amount: 10000, note: '', version: 1 }))
  const context = { accountId: null, token: '', book, month, ready: true }
  store.connect(context, () => entries)
  const body = { version: 0, effectiveMonth: month, plan }, preview = await store.query('previewPlan', body)
  await store.write('savePlan', { ...body, impactToken: preview.impactToken, requestId: req() })
  book.members[0].role = 'member'; book.members[1].role = 'owner'; store.connect(context, () => entries)
  assert.ok(await store.query('detail', reserve))
  assert.equal(await store.write('fund', reserve, { requestId: req(), fundVersion: 0, id: 'forbidden', kind: 'deposit', amount: 100, date: today }), undefined)
  assert.equal(await store.write('setLink', entries[1].id, { requestId: req(), entryVersion: 1, linkVersion: 0, itemId: reserve }), undefined)
  assert.ok(await store.write('setLink', entries[0].id, { requestId: req(), entryVersion: 1, linkVersion: 0, itemId: reserve }))
  assert.equal((await store.query('detail', reserve)).balance.availableAmount, -10000)
})
await check('资金流水分页不丢审计，余额始终按全部有效记录计算', async () => {
  let fv = (await api.detail(reserve)).item.fundVersion
  for (let i = 0; i < 25; i++) await api.fund(reserve, { requestId: req(), fundVersion: fv++, id: 'page-' + i, kind: 'deposit', amount: 1, date: today })
  const first = await api.detail(reserve), second = await api.detail(reserve, first.nextRecordCursor)
  const ids = [...first.records, ...second.records].map(r => r.id)
  assert.equal(new Set(ids).size, ids.length); assert.equal(first.balance.availableAmount, second.balance.availableAmount); assert.ok(second.records.length); assert.equal(second.nextRecordCursor, null)
})
console.log(`预算项目执行检查完成：${passed} 项通过。`)
