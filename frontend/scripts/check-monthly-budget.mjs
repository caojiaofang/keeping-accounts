import assert from 'node:assert/strict'
import path from 'node:path'
import { createPinia, setActivePinia } from 'pinia'
import { moduleUrl, root } from './test-modules.mjs'

const { previousBudgetMonth, monthlyBudgetUsage } = await import(await moduleUrl(path.join(root, 'common/services/monthly-budget.ts')))
const { useLedgerStore } = await import(await moduleUrl(path.join(root, 'store/ledger.ts')))
const { useBudgetStore } = await import(await moduleUrl(path.join(root, 'store/budget.ts')))
const storage = new Map(), copy = value => structuredClone(value)
globalThis.uni = { getStorageSync: key => copy(storage.get(key)), setStorageSync: (key, value) => storage.set(key, copy(value)), removeStorageSync: key => storage.delete(key), showToast() {} }
let passed = 0
async function check(name, fn) { await fn(); passed++; console.log('通过：' + name) }
setActivePinia(createPinia())
const ledger = useLedgerStore(), budget = useBudgetStore()
ledger.hydrate()
const date = new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10), month = date.slice(0, 7)
const [year, value] = month.split('-').map(Number), next = `${year + (value === 12 ? 1 : 0)}-${String(value % 12 + 1).padStart(2, '0')}`
const group = '20000000-0000-4000-8000-000000000001', item = '30000000-0000-4000-8000-000000000001'
const plan = { plannedIncome: 0, groups: [{ id: group, name: '本月预算' }], items: [{ id: item, groupId: group, name: '餐饮', kind: 'expense', monthlyAmount: 10000, dueDay: null, reserve: null, note: '' }] }
const api = budget.apiForBook(ledger.activeBook)
await check('上月按日历计算，跨年与最早月份边界正确', async () => {
  assert.equal(previousBudgetMonth('2027-01'), '2026-12'); assert.equal(previousBudgetMonth('2024-03'), '2024-02'); assert.equal(previousBudgetMonth('2000-01'), null)
})
await check('月预算保存可重放，新月份不会自动生成预算', async () => {
  const body = { requestId: 'monthly-create', version: 0, plan }
  const result = await api.saveMonthly(month, body)
  assert.deepEqual(await api.saveMonthly(month, body), result)
  assert.equal((await api.monthly(next)).state, 'not_set')
  assert.equal((await api.month(next)).state, 'not_set')
})
let entryId
await check('本地记账选择月预算后只累计一次，超支显示精确差额', async () => {
  assert.equal(await ledger.saveEntry({ bookId: ledger.activeBookId, type: 'expense', categoryId: 'food', amount: 12000, date, account: '微信', note: '月预算回归', budgetItemId: item, budgetLinkVersion: 0 }, undefined, 'monthly-bill'), true)
  entryId = 'monthly-bill'
  const execution = await api.monthlyExecution(month)
  assert.equal(execution.items[0].expenseAmount, 12000)
  assert.equal(execution.items[0].remainingAmount, -2000)
  assert.equal(monthlyBudgetUsage({ ...execution, expenseTotal: 15000 }).over, 5000)
})
await check('手动增加额度不改账单，旧版本保存会冲突', async () => {
  const changed = copy(plan); changed.items[0].monthlyAmount = 20000
  await api.saveMonthly(month, { requestId: 'monthly-edit', version: 1, plan: changed })
  await assert.rejects(api.saveMonthly(month, { requestId: 'monthly-conflict', version: 1, plan }))
  assert.equal((await api.monthlyExecution(month)).items[0].remainingAmount, 8000)
})
await check('沿用预算只复制额度，上月消费不计入新月份', async () => {
  const source = await api.monthly(month)
  await api.saveMonthly(next, { requestId: 'monthly-copy', version: 0, plan: source.plan })
  const result = await api.monthlyExecution(next)
  assert.equal(result.expenseTotal, 0); assert.equal(result.items[0].remainingAmount, 20000)
})
await check('删除已关联项目保留账单并解除本月关联，其他月份不受影响', async () => {
  await api.saveMonthly(month, { requestId: 'monthly-remove', version: 2, plan: { ...plan, items: [] } })
  const result = await api.monthlyExecution(month)
  assert.ok(result.expenseTotal >= 12000); assert.equal(result.unlinkedExpenseTotal, result.expenseTotal); assert.equal(result.linkedExpenseTotal, 0)
  assert.equal((await api.link(entryId)).link.isDeleted, true)
  assert.equal((await api.monthlyExecution(next)).items[0].remainingAmount, 20000)
  await api.saveMonthly(month, { requestId: 'monthly-restore', version: 3, plan })
  assert.equal(await ledger.deleteEntry(entryId, 1), true)
  assert.equal((await api.monthlyExecution(month)).items[0].remainingAmount, 10000)
})
await check('普通家庭成员可创建、修改、删除月预算，删除重建持续校验版本', async () => {
  const member = { id: 'shared-monthly', kind: 'shared', currentUserId: 'member', members: [{ userId: 'member', status: 'active', role: 'member' }] }
  budget.connect({ accountId: null, token: '', ready: true, month, book: member }, () => [])
  assert.ok(await budget.write('saveMonthly', month, { requestId: 'member-save', version: 0, plan }))
  assert.ok(await budget.write('saveMonthly', month, { requestId: 'member-edit', version: 1, plan }))
  const body = { requestId: 'member-delete', version: 2 }
  assert.ok(await budget.write('deleteMonthly', month, body))
  assert.equal(budget.month.state, 'not_set'); assert.equal(budget.month.version, 3)
  assert.ok(await budget.write('deleteMonthly', month, body))
  assert.ok(await budget.write('saveMonthly', month, { requestId: 'member-recreate', version: 3, plan }))
  assert.equal(budget.month.version, 4)
  assert.equal(await budget.write('deleteMonthly', month, { requestId: 'stale-delete', version: 2 }), undefined)
  assert.ok(budget.conflict)
  member.members[0].status = 'left'
  budget.connect({ accountId: null, token: '', ready: true, month, book: member }, () => [])
  assert.equal(await budget.write('saveMonthly', month, { requestId: 'left-save', version: 1, plan }), undefined)
  assert.equal(await budget.write('deleteMonthly', month, { requestId: 'left-delete', version: 4 }), undefined)
})
console.log(`独立月预算检查完成：${passed} 项通过。`)
