import assert from 'node:assert/strict'
import path from 'node:path'
import { readFile } from 'node:fs/promises'
import { createPinia, setActivePinia } from 'pinia'
import { moduleUrl, root } from './test-modules.mjs'
const { createBudgetApi } = await import(await moduleUrl(path.join(root, 'common/api/budget.ts')))
const { createBudgetDemo, budgetTotals } = await import(await moduleUrl(path.join(root, 'common/services/budget-demo.ts')))
const { useBudgetStore } = await import(await moduleUrl(path.join(root, 'store/budget.ts')))
const fixture = JSON.parse(await readFile(path.join(root, '../backend/docs/examples/family-budget-plan.json'), 'utf8'))
const date = new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10), month = date.slice(0, 7)
const clone = value => JSON.parse(JSON.stringify(value))
let passed = 0, sequence = 0
const requestId = () => `request-${++sequence}`
async function check(name, fn) { await fn(); passed++; console.log('通过：' + name) }
let handler, calls = [], data = new Map()
globalThis.uni = {
  getStorageSync: key => data.get(key), setStorageSync: (key, value) => data.set(key, clone(value)), removeStorageSync: key => data.delete(key),
  request(options) { calls.push(options); Promise.resolve().then(() => handler(options)).then(value => options.success(value), () => options.fail({ errMsg: 'timeout' })) },
}
const ok = data => ({ statusCode: 200, data })
let ledger = [], stored
const demo = createBudgetDemo('demo-book', () => ledger, value => { stored = clone(value) })
async function setup(api = demo) {
  const body = { version: 0, effectiveMonth: month, plan: clone(fixture) }
  const preview = await api.previewPlan(body)
  return api.savePlan({ ...body, requestId: requestId(), impactToken: preview.impactToken })
}
const saving = fixture.items[5].id, reserve = fixture.items[3].id
await check('完整示例按叶子汇总 15600，生活费 4040，不重复计算父级', async () => {
  const totals = budgetTotals(fixture); assert.equal(totals.totalAllocated, 1560000); assert.equal(totals.unallocated, 0); assert.equal(totals.groupTotals[3].amount, 404000)
})
await check('体验预算首次未设置，保存后预览，确认唯一快照不创造资金', async () => {
  assert.equal((await demo.month(month)).state, 'not_set'); await setup()
  const preview = await demo.month(month); assert.equal(preview.state, 'preview')
  const body = { requestId: requestId(), sourceRevisionId: preview.sourceRevisionId, planVersion: 1 }
  assert.deepEqual(await demo.confirm(month, body), await demo.confirm(month, body))
  assert.equal((await demo.detail(saving)).balance.availableAmount, 0)
})
await check('分次存入与原请求重放只记一次，替代修正保留审计', async () => {
  const body = { requestId: requestId(), fundVersion: 0, id: 'deposit-1', kind: 'deposit', amount: 420000, date }
  assert.deepEqual(await demo.fund(saving, body), await demo.fund(saving, body))
  const corrected = await demo.correctFund(saving, body.id, { requestId: requestId(), fundVersion: 1, recordVersion: 1, replacement: { id: 'deposit-2', amount: 400000, date } })
  assert.equal(corrected.balance.availableAmount, 400000); assert.equal(corrected.record.version, 2); assert.equal(corrected.replacement.replacesId, body.id)
  assert.equal((await demo.detail(saving)).records.length, 2)
})
await check('实际账单只计一次，删除原账单自动恢复用途资金', async () => {
  ledger = [{ id: 'bill', bookId: 'demo-book', type: 'expense', amount: 50000, date, note: '', version: 1 }]
  await demo.setLink('bill', { requestId: requestId(), entryVersion: 1, linkVersion: 0, itemId: saving })
  const result = await demo.execution(month); assert.equal(result.expenseTotal, 50000); assert.equal(result.linkedExpenseTotal, 50000)
  assert.equal((await demo.detail(saving)).balance.availableAmount, 350000)
  ledger = []; assert.equal((await demo.detail(saving)).balance.availableAmount, 400000)
})
await check('存入不能同时计作结转，目标不会创造第二份资金', async () => {
  await assert.rejects(demo.createGoal(saving, { requestId: requestId(), id: 'goal-invalid', fundVersion: 4, startDate: month + '-01', targetDate: date, targetAmount: 5000000, carryInAmount: 400000 }))
  await demo.createGoal(saving, { requestId: requestId(), id: 'goal', fundVersion: 4, startDate: month + '-01', targetDate: date, targetAmount: 5000000, carryInAmount: 0 })
  assert.equal((await demo.detail(saving)).balance.availableAmount, 400000)
  assert.equal((await demo.fundingPreview(saving, 'goal')).fundedAmount, 400000)
})
await check('失败的多月均摊整体回滚；体验存储可恢复，资金不按月累加', async () => {
  const before = clone(stored)
  await assert.rejects(demo.equalize({ requestId: requestId(), version: 1, itemId: reserve, startMonth: month, endMonth: month, totalAmount: 610000, impactToken: 'expired' }))
  assert.deepEqual(stored, before)
  const restored = createBudgetDemo('demo-book', () => [], () => {}, stored)
  assert.equal((await restored.detail(saving)).balance.availableAmount, 400000)
})
await check('API 透传 Bearer、DELETE 正文、目标 ID 与双分页游标', async () => {
  calls = []; handler = () => ok({})
  const api = createBudgetApi('token', 'book /')
  const body = { requestId: requestId(), entryVersion: 1, linkVersion: 2 }
  await api.removeLink('entry /', body); assert.equal(calls[0].method, 'DELETE'); assert.deepEqual(calls[0].data, body); assert.equal(calls[0].header.Authorization, 'Bearer token')
  assert.ok(calls[0].url.includes('book%20%2F'))
  await api.detail('item', 'record', 'entry'); assert.ok(calls[1].url.includes('beforeRecordId=record&beforeEntryId=entry'))
})
function context(accountId = 'user-a', bookId = 'book-a', token = 'token-a') { return { accountId, token, book: { id: bookId, kind: 'personal' }, month, ready: true } }
function response(options) {
  const url = new URL(options.url), bookId = decodeURIComponent(url.pathname.split('/')[4]), base = { schemaVersion: 1, bookId }
  if (url.pathname.endsWith('/plan')) return ok({ ...base, version: 0, revisions: [] })
  if (url.pathname.endsWith('/execution')) return ok({ ...base, month, items: [], expenseTotal: 0 })
  if (url.pathname.endsWith('/items')) return ok({ ...base, items: [], nextCursor: null })
  return ok({ ...base, month, state: 'not_set' })
}
function fresh(c = context()) { setActivePinia(createPinia()); const store = useBudgetStore(); store.connect(c, () => []); handler = response; calls = []; return store }
await check('账号切换后丢弃迟到读取；缓存不写入任何云端预算或 token', async () => {
  const store = fresh(); const delayed = []
  handler = options => new Promise(resolve => delayed.push(() => resolve(response(options))))
  const task = store.load(); await new Promise(resolve => setTimeout(resolve, 0))
  store.connect(context('user-b', 'book-b', 'token-b'), () => [])
  delayed.forEach(resolve => resolve()); assert.equal(await task, false); assert.equal(store.plan, null); assert.equal(store.loading, false)
  handler = response; await store.load(); assert.equal(store.plan.bookId, 'book-b')
  assert.ok(![...data.keys()].some(key => key.includes('token-b')))
})
await check('切换账本和月份清空旧预算，同账号更换 token 重建数据源', async () => {
  const store = fresh(); await store.load()
  store.connect({ ...context('user-a', 'book-b', 'new-token'), month: '2027-01' }, () => []); assert.equal(store.plan, null)
  handler = o => { const r = response(o); if (r.data.month) r.data.month = '2027-01'; return r }
  await store.load(); assert.equal(store.month.month, '2027-01'); assert.ok(calls.at(-1).header.Authorization.includes('new-token'))
})
await check('网络失败保留冻结请求，显式重试沿用原 requestId 与原正文', async () => {
  const store = fresh(); let bodySeen, writes = 0
  handler = o => { if (o.method === 'POST') { writes++; bodySeen = clone(o.data); if (writes === 1) throw Error(); return ok({ saved: true }) } return response(o) }
  const body = { requestId: requestId(), fundVersion: 0, id: 'fund', kind: 'deposit', amount: 420000, date }
  assert.equal(await store.write('fund', saving, body), undefined); assert.ok(store.retryAvailable)
  body.amount = 1; assert.deepEqual(await store.retry(), { saved: true }); assert.equal(bodySeen.amount, 420000); assert.equal(store.retryAvailable, false)
})
await check('409 不显示保存成功，清除旧视图并要求重新读取；403 成员只读', async () => {
  const store = fresh(); await store.load(); handler = () => ({ statusCode: 409, data: { detail: '版本已变化' } })
  await store.write('fund', saving, { requestId: requestId() }); assert.ok(store.conflict); assert.equal(store.plan, null); assert.equal(store.retryAvailable, false)
  store.connect({ ...context(), book: { id: 'shared', kind: 'shared', currentUserId: 'member', members: [{ userId: 'member', role: 'member', status: 'active' }] } }, () => [])
  const count = calls.length; await store.write('fund', saving, {}); assert.equal(calls.length, count); assert.match(store.error, /创建者/)
})
await check('写入成功后刷新失败单独提示，不能重试为第二次存入', async () => {
  const store = fresh(); handler = o => o.method === 'POST' ? ok({ saved: true }) : ({ statusCode: 503, data: { detail: '读取失败' } })
  assert.deepEqual(await store.write('fund', saving, { requestId: requestId() }), { saved: true }); assert.match(store.refreshError, /已保存/); assert.equal(store.retryAvailable, false)
})
await check('响应归属错误和401不会保留旧预算；体验操作不会发送网络请求', async () => {
  const store = fresh(); await store.load(); handler = () => ok({ schemaVersion: 1, bookId: 'foreign' }); assert.equal(await store.load(), false); assert.match(store.error, /归属/)
  handler = () => ({ statusCode: 401, data: { detail: '登录过期' } }); await store.load(); assert.equal(store.plan, null)
  calls = []; store.connect(context(null, 'demo-new', ''), () => []); await store.load(); assert.equal(calls.length, 0); assert.equal(store.month.state, 'not_set')
})
await check('精确均摊 610000 分合计准确，采用后覆盖可完整恢复继承', async () => {
  const api = createBudgetDemo('allocation', () => [], () => {})
  await setup(api)
  const end = new Date(Number(month.slice(0, 4)), Number(month.slice(5, 7)) + 1, 1)
  const endMonth = `${end.getFullYear()}-${String(end.getMonth() + 1).padStart(2, '0')}`
  const body = { version: 1, itemId: reserve, totalAmount: 610000, startMonth: month, endMonth }
  const preview = await api.previewEqualization(body)
  assert.equal(preview.schedule.reduce((n, row) => n + row.amount, 0), 610000)
  const result = await api.equalize({ ...body, impactToken: preview.impactToken, requestId: requestId() })
  const first = result.months[0]
  const restored = await api.updateMonth(month, { requestId: requestId(), version: first.version, override: { plannedIncome: null, itemAmounts: [] } })
  assert.equal(restored.plan.items.find(i => i.id === reserve).monthlyAmount, 52000)
})
await check('体验还贷 50400 使用50000 余400，续建只分配余款', async () => {
  let bills = [{ id: 'mortgage', bookId: 'mortgage', type: 'expense', date, amount: 5000000, version: 1, note: '' }]
  const api = createBudgetDemo('mortgage', () => bills, () => {})
  await setup(api)
  await api.fund(saving, { requestId: requestId(), fundVersion: 0, id: 'opening', kind: 'opening', amount: 5040000, date: month + '-01' })
  const terms = { startDate: month + '-01', targetDate: date, targetAmount: 5000000, carryInAmount: 5040000 }
  await api.createGoal(saving, { ...terms, requestId: requestId(), fundVersion: 1, id: 'first' })
  await api.setLink('mortgage', { requestId: requestId(), entryVersion: 1, linkVersion: 0, itemId: saving, goalPeriodId: 'first' })
  assert.equal((await api.detail(saving)).balance.availableAmount, 40000)
  await api.updateGoal(saving, 'first', { ...terms, requestId: requestId(), fundVersion: 3, version: 1, state: 'closed' })
  const next = new Date(date + 'T00:00:00Z'); next.setUTCDate(next.getUTCDate() + 1)
  const startDate = next.toISOString().slice(0, 10)
  await api.createGoal(saving, { requestId: requestId(), fundVersion: 4, id: 'next', startDate, targetDate: startDate, targetAmount: 2500000, carryInAmount: 40000 })
  assert.equal((await api.detail(saving)).balance.availableAmount, 40000)
  assert.equal((await api.detail(saving)).goals[0].forecast.gapAmount, 0)
})
await check('保存响应迟到不回填其他账号，重复点击不发送第二次写入', async () => {
  const store = fresh(); let release, count = 0
  handler = o => o.method === 'POST' ? new Promise(resolve => { count++; release = () => resolve(ok({ saved: true })) }) : response(o)
  const task = store.write('fund', saving, { requestId: requestId() }); await new Promise(resolve => setTimeout(resolve, 0))
  assert.equal(await store.write('fund', saving, { requestId: requestId() }), undefined); assert.equal(count, 1)
  store.connect(context('user-b', 'book-b', 'b'), () => []); release(); assert.equal(await task, undefined); assert.equal(store.writing, false); assert.equal(store.plan, null)
})
await check('体验存储失败不显示成功，也不留下重复期初或存入', async () => {
  let broken = false
  const api = createBudgetDemo('storage', () => [], () => { if (broken) throw Error('本地空间不足') })
  await setup(api); broken = true
  await assert.rejects(api.fund(saving, { requestId: requestId(), fundVersion: 0, id: 'failed', kind: 'opening', amount: 50000, date }))
  broken = false; assert.equal((await api.detail(saving)).balance.availableAmount, 0)
})
await check('账单刷新不能打断在途保存或开放第二次写入', async () => {
  const store = fresh(); await store.load(); let release, count = 0
  handler = o => o.method === 'POST' ? new Promise(resolve => { count++; release = () => resolve(ok({ saved: true })) }) : response(o)
  const task = store.write('fund', saving, { requestId: requestId() }); await new Promise(resolve => setTimeout(resolve, 0))
  store.ledgerChanged('账单已刷新'); assert.equal(store.writing, true)
  assert.equal(await store.write('fund', saving, { requestId: requestId() }), undefined); assert.equal(count, 1)
  release(); assert.deepEqual(await task, { saved: true }); assert.equal(store.writing, false); assert.equal(store.plan.bookId, 'book-a')
})
await check('响应未确认时新写入不能覆盖原请求；刷新后仍能按原正文重试', async () => {
  const store = fresh(); let writes = []
  handler = o => { if (o.method === 'POST') { writes.push(clone(o.data)); if (writes.length === 1) throw Error(); return ok({ saved: true }) } return response(o) }
  const body = { requestId: requestId(), id: 'pending-fund', amount: 420000 }
  await store.write('fund', saving, body); assert.ok(store.retryAvailable)
  assert.equal(await store.write('fund', saving, { ...body, requestId: requestId(), amount: 1 }), undefined)
  store.ledgerChanged(); await store.load(); assert.ok(store.retryAvailable)
  assert.deepEqual(await store.retry(), { saved: true }); assert.equal(writes.length, 2); assert.deepEqual(writes[0], writes[1])
})
await check('离页后仍保留原确认操作类型，重放确认不等于提交单月调整', async () => {
  const store = fresh(); let failed = false, confirmed
  handler = o => {
    if (o.method === 'POST') { if (!failed) { failed = true; throw Error() } confirmed = clone(o.data); return ok({ schemaVersion: 1, bookId: 'book-a', month, state: 'confirmed', version: 1 }) }
    return response(o)
  }
  const body = { requestId: requestId(), sourceRevisionId: 'original', planVersion: 1 }
  await store.write('confirm', month, body); assert.equal(store.pendingMethod, 'confirm'); assert.ok(store.retryAvailable)
  store.connect(context(), () => []); await store.load(); assert.equal(store.pendingMethod, 'confirm')
  await store.retry(); assert.deepEqual(confirmed, body); assert.equal(store.pendingMethod, null)
  assert.equal(calls.filter(o => o.method === 'PUT').length, 0)
})
console.log(`家庭预算检查完成：${passed} 项通过；仅模拟服务与独立体验数据。`)
