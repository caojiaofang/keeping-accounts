import assert from 'node:assert/strict'
import path from 'node:path'
import { createPinia, setActivePinia } from 'pinia'
import { moduleUrl, root } from './test-modules.mjs'
const { useBudgetSummaryStore } = await import(await moduleUrl(path.join(root, 'store/budget-summary.ts')))
const { useBudgetStore } = await import(await moduleUrl(path.join(root, 'store/budget.ts')))
const { useLedgerStore } = await import(await moduleUrl(path.join(root, 'store/ledger.ts')))
const { ApiError } = await import(await moduleUrl(path.join(root, 'common/api/request.ts')))
const context = (changes = {}) => ({ accountId: 'user-a', token: 'token-a', ready: true, month: '2026-11', book: { id: 'book-a', kind: 'personal' }, ...changes })
const value = (changes = {}) => ({ schemaVersion: 1, bookId: 'book-a', month: '2026-11', state: 'confirmed', version: 1, budgetAmount: 10000, expenseAmount: 2000, remainingAmount: 8000, overAmount: 0, generatedAt: 1000, ...changes })
const tick = () => new Promise(resolve => setTimeout(resolve, 0))
const fresh = () => { setActivePinia(createPinia()); return useBudgetSummaryStore() }
let passed = 0
async function check(name, action) { await action(); passed++; console.log('通过：' + name) }

await check('两个页面同时读取合并为一次请求，短期重复读取直接复用结果', async () => {
  const store = fresh(); let calls = 0, finish
  store.connect(context(), { summary: () => { calls++; return new Promise(resolve => { finish = resolve }) } })
  const first = store.load(), second = store.load(true)
  await tick(); assert.equal(calls, 1)
  finish(value()); assert.ok(await first); assert.ok(await second)
  assert.ok(await store.load()); assert.equal(calls, 1); assert.equal(store.data.remainingAmount, 8000)
})
await check('过期结果先保留展示再更新，失效通知使未过期结果也重新查询', async () => {
  const store = fresh(), now = Date.now; let clock = 10000, calls = 0
  Date.now = () => clock
  try {
    store.connect(context(), { summary: async () => { calls++; return value() } })
    await store.load(); clock += 3001
    const request = store.load(); assert.equal(store.data.budgetAmount, 10000)
    await request; assert.equal(calls, 2)
    store.invalidate(); assert.equal(store.data.budgetAmount, 10000)
    await store.load(); assert.equal(calls, 3)
  } finally { Date.now = now }
})
await check('切换账本、月份、账号和凭据立即清空，迟到响应不能覆盖新范围', async () => {
  const store = fresh(); let finish
  store.connect(context(), { summary: () => new Promise(resolve => { finish = resolve }) })
  const task = store.load(); await tick()
  store.connect(context({ month: '2026-12' }), { summary: async () => value({ month: '2026-12' }) })
  assert.equal(store.data, null)
  await store.load(); finish(value()); assert.equal(await task, false); assert.equal(store.data.month, '2026-12')
  for (const changes of [{ accountId: 'user-b' }, { token: 'new-token' }, { book: { id: 'book-b', kind: 'personal' } }]) {
    store.connect(context(changes), { summary: async () => value() }); assert.equal(store.data, null)
  }
})
await check('成员权限变化清除缓存，失权响应不能继续显示旧金额；网络失败保留已读结果并提示', async () => {
  const store = fresh(); let failure
  const api = { summary: async () => { if (failure) throw failure; return value() } }
  store.connect(context(), api); await store.load()
  failure = new ApiError('暂时不可用', 503)
  assert.equal(await store.load(true), false); assert.ok(store.data); assert.match(store.error, /暂时/)
  failure = new ApiError('已无权访问', 403)
  assert.equal(await store.load(true), false); assert.equal(store.data, null)
  store.connect(context({ book: { id: 'book-a', kind: 'shared', members: [{ userId: 'user-a', status: 'left' }] } }), api)
  assert.equal(store.data, null)
})
await check('错误归属、错误余额和未设置时伪造额度都会被拒绝', async () => {
  for (const bad of [value({ bookId: 'other' }), value({ remainingAmount: 9999 }), value({ state: 'not_set' })]) {
    const store = fresh(); store.connect(context(), { summary: async () => bad })
    assert.equal(await store.load(), false); assert.equal(store.data, null)
  }
})
await check('账单刷新只让摘要失效，不自动加载四个完整预算接口', async () => {
  setActivePinia(createPinia()); const budget = useBudgetStore(), summary = useBudgetSummaryStore()
  const calls = []
  globalThis.uni = { request(o) { calls.push(o.url); o.success({ statusCode: 200, data: value() }) } }
  budget.connect(context(), () => [])
  assert.ok(await summary.load()); budget.ledgerChanged()
  assert.ok(await summary.load())
  assert.equal(calls.length, 2); assert.ok(calls.every(url => url.endsWith('/summary')))
})
await check('本地创建、记账、调额和删除预算后摘要均能刷新为正确金额', async () => {
  const storage = new Map()
  globalThis.uni = { getStorageSync: key => storage.get(key), setStorageSync: (key, v) => storage.set(key, structuredClone(v)), removeStorageSync: key => storage.delete(key), showToast() {} }
  setActivePinia(createPinia()); const ledger = useLedgerStore(), budget = useBudgetStore(), summary = useBudgetSummaryStore()
  ledger.hydrate()
  const group = '20000000-0000-4000-8000-000000000001', item = '30000000-0000-4000-8000-000000000001'
  const plan = { plannedIncome: 0, groups: [{ id: group, name: '本月' }], items: [{ id: item, groupId: group, name: '餐饮', kind: 'expense', monthlyAmount: 10000, dueDay: null, reserve: null, note: '' }] }
  const month = ledger.selectedMonth
  await budget.write('saveMonthly', month, { requestId: 'create', version: 0, plan }); await summary.load()
  const before = summary.data.expenseAmount
  await ledger.saveEntry({ bookId: ledger.activeBookId, type: 'expense', categoryId: 'food', amount: 1000, note: '', date: month + '-01', account: '微信' })
  await summary.load(); assert.equal(summary.data.expenseAmount, before + 1000)
  plan.items[0].monthlyAmount = 20000
  await budget.write('saveMonthly', month, { requestId: 'edit', version: 1, plan }); await summary.load()
  assert.equal(summary.data.budgetAmount, 20000)
  await budget.write('deleteMonthly', month, { requestId: 'delete', version: 2 }); await summary.load()
  assert.equal(summary.data.state, 'not_set'); assert.equal(summary.data.budgetAmount, null)
})
console.log(`预算卡片摘要检查完成：${passed} 项通过。`)
