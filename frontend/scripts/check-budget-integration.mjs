/** 由隔离 PostgreSQL pytest 驱动；只允许本机 HTTP，不直接访问开发或正式服务。 */
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import path from 'node:path'
import { createPinia, setActivePinia } from 'pinia'
import { moduleUrl, root } from './test-modules.mjs'

let input = ''
for await (const chunk of process.stdin) input += chunk
const config = JSON.parse(input)
const base = new URL(config.baseUrl)
assert.equal(base.protocol, 'http:'); assert.equal(base.hostname, '127.0.0.1'); assert.ok(base.port)
let loseResponse = false, requests = 0
globalThis.uni = {
  getStorageSync() {}, setStorageSync() { throw Error('云端预算不应写入本地缓存') },
  request(options) {
    // 只将真实 uni.request 的公开地址替换为测试端口，路由、方法、正文和响应均走 HTTP。
    const url = new URL(options.url); requests++
    fetch(new URL(url.pathname + url.search, base), {
      method: options.method, headers: { ...options.header, 'Content-Type': 'application/json' },
      body: options.data === undefined ? undefined : JSON.stringify(options.data),
      signal: AbortSignal.timeout(10000),
    }).then(async response => {
      const data = response.status === 204 ? undefined : await response.json()
      if (loseResponse && options.method === 'POST' && url.pathname.endsWith('/funds') && response.ok) {
        loseResponse = false; options.fail({ errMsg: 'timeout after commit' }); return
      }
      options.success({ statusCode: response.status, data })
    }).catch(() => options.fail({ errMsg: 'network failure' }))
  },
}
const { createBudgetApi } = await import(await moduleUrl(path.join(root, 'common/api/budget.ts')))
const { apiRequest } = await import(await moduleUrl(path.join(root, 'common/api/request.ts')))
const { useBudgetStore } = await import(await moduleUrl(path.join(root, 'store/budget.ts')))
const token = 'user-0', bookId = config.books[0], month = '2026-11', date = '2026-11-06'
const api = createBudgetApi(token, bookId), saving = config.plan.items[5].id, reserve = config.plan.items[3].id
const requestId = randomUUID, copy = value => JSON.parse(JSON.stringify(value))
const ledger = (path, method = 'GET', data, who = 0) => apiRequest('/api/v1' + path, { method, data, token: `user-${who}` })
let entries = [], passed = 0
async function check(name, fn) { await fn(); passed++; console.log('通过真实联调：' + name) }
async function savePlan(client, plan, version = 0, effectiveMonth = month) {
  const body = { plan, version, effectiveMonth }, preview = await client.previewPlan(body)
  return client.savePlan({ ...body, requestId: requestId(), impactToken: preview.impactToken })
}
async function bill(amount, when = date, book = bookId, who = 0) {
  const result = await ledger('/entries', 'POST', { id: randomUUID(), bookId: book, type: 'expense', categoryId: 'house', amount, date: when, account: '实际账单', note: '前后端联调' }, who)
  entries.push(result); return result
}
function editBody(entry, changes) {
  return { ...Object.fromEntries(['bookId', 'type', 'categoryId', 'amount', 'date', 'account', 'note', 'version'].map(key => [key, entry[key]])), ...changes }
}
function storeFor(who = 0, book = bookId, kind = 'personal') {
  setActivePinia(createPinia()); const store = useBudgetStore()
  store.connect({ accountId: config.users[who], token: `user-${who}`, book: { id: book, kind, currentUserId: config.users[who], members: config.users.slice(0, 2).map((userId, i) => ({ userId, status: 'active', role: i ? 'member' : 'owner' })) }, month, ready: true }, () => entries)
  return store
}
let store, mortgage, firstGoal, savedTerms
await check('旧预算独立；完整家庭方案、历史、未来和零实际', async () => {
  assert.equal((await api.month(month)).state, 'not_set')
  await ledger(`/books/${bookId}/budget`, 'PUT', { budget: 500000, version: 1 })
  await savePlan(api, config.plan); store = storeFor(); assert.ok(await store.load())
  assert.equal(store.month.totals.totalAllocated, 1560000); assert.equal(store.month.totals.groupTotals[3].amount, 404000)
  assert.equal(store.execution.expenseTotal, 0); assert.equal((await api.detail(saving)).balance.availableAmount, 0)
  assert.equal((await api.month('2026-10')).state, 'not_set'); assert.equal((await api.month('2027-01')).state, 'preview')
  assert.equal((await ledger('/ledger')).books.find(b => b.id === bookId).budget, 500000)
})
await check('真实提交后丢响应，Pinia 原 ID 重放只存一次；旧版本冲突', async () => {
  const body = { requestId: requestId(), id: randomUUID(), fundVersion: 0, kind: 'opening', amount: 5040000, date: '2026-11-01' }
  loseResponse = true; assert.equal(await store.write('fund', saving, body), undefined); assert.ok(store.retryAvailable)
  body.amount = 1; assert.ok(await store.retry()); assert.equal(store.retryAvailable, false)
  const detail = await api.detail(saving); assert.equal(detail.balance.availableAmount, 5040000); assert.equal(detail.records.length, 1)
  assert.equal(await store.write('fund', saving, { ...body, requestId: requestId(), id: randomUUID() }), undefined)
  assert.ok(store.conflict); assert.equal(store.plan, null); assert.ok(await store.load()); assert.equal(store.conflict, false)
})
await check('原账单还贷、闭期与余款续建；资金和消费各只算一次', async () => {
  savedTerms = { startDate: '2026-11-01', targetDate: date, targetAmount: 5000000, carryInAmount: 5040000 }
  firstGoal = randomUUID(); await api.createGoal(saving, { ...savedTerms, requestId: requestId(), id: firstGoal, fundVersion: 1 })
  mortgage = await bill(5000000)
  await store.write('setLink', mortgage.id, { requestId: requestId(), entryVersion: 1, linkVersion: 0, itemId: saving, goalPeriodId: firstGoal })
  let detail = await api.detail(saving); assert.deepEqual([detail.goals[0].collectedAmount, detail.goals[0].usedAmount, detail.balance.availableAmount], [5040000, 5000000, 40000])
  await api.updateGoal(saving, firstGoal, { ...savedTerms, requestId: requestId(), fundVersion: 3, version: 1, state: 'closed' })
  await api.createGoal(saving, { requestId: requestId(), id: randomUUID(), fundVersion: 4, startDate: '2026-11-07', targetDate: '2027-05-06', targetAmount: 2500000, carryInAmount: 40000 })
  detail = await api.detail(saving); assert.equal(detail.balance.availableAmount, 40000); assert.equal(detail.goals[1].forecast.gapAmount, 2460000)
  assert.equal((await api.execution(month)).expenseTotal, 5000000); assert.equal((await ledger('/ledger')).entries.length, 1)
})
await check('旧账单编辑／删除同步闭期资金；关联墓碑防过时覆盖', async () => {
  const updated = await ledger(`/entries/${mortgage.id}`, 'PUT', editBody(mortgage, { amount: 4950000 }))
  let detail = await api.detail(saving); assert.equal(detail.balance.availableAmount, 90000); assert.equal(detail.goals[0].usedAmount, 4950000); assert.equal(detail.goals[0].state, 'closed')
  await assert.rejects(api.removeLink(mortgage.id, { requestId: requestId(), entryVersion: 1, linkVersion: 1 }), e => e.status === 409)
  await ledger(`/entries/${mortgage.id}?version=${updated.version}`, 'DELETE')
  entries = entries.filter(e => e.id !== mortgage.id); store.ledgerChanged(); await store.load()
  assert.equal(store.execution.expenseTotal, 0); detail = await api.detail(saving); assert.equal(detail.balance.availableAmount, 5040000)
})
await check('单月人情 +200、后续学费 +100、6100 均摊不改实际钱', async () => {
  const original = await api.month(month)
  await api.updateMonth(month, { requestId: requestId(), version: original.version, override: { plannedIncome: null, itemAmounts: [{ itemId: config.plan.items.at(-1).id, amount: 60000 }] } })
  assert.equal((await api.month(month)).totals.unallocated, -20000); assert.equal((await api.month('2026-12')).totals.unallocated, 0)
  const later = copy(config.plan); later.items[1].monthlyAmount = 120000; await savePlan(api, later, 1, '2027-01')
  assert.equal((await api.month(month)).plan.items[1].monthlyAmount, 110000); assert.equal((await api.month('2027-01')).plan.items[1].monthlyAmount, 120000)
  const body = { version: 2, itemId: reserve, totalAmount: 610000, startMonth: month, endMonth: '2027-10' }, preview = await api.previewEqualization(body)
  assert.equal(preview.schedule.reduce((sum, row) => sum + row.amount, 0), 610000)
  await api.equalize({ ...body, requestId: requestId(), impactToken: preview.impactToken })
  assert.equal((await api.detail(reserve)).balance.availableAmount, 0); assert.equal((await api.month(month)).plan.items.at(-1).monthlyAmount, 60000)
})
await check('跨月账单归集；不足缴费、替换关联和取消都保留原账单', async () => {
  await api.fund(reserve, { requestId: requestId(), id: randomUUID(), fundVersion: 0, kind: 'deposit', amount: 52000, date })
  const paid = await bill(610000); await api.setLink(paid.id, { requestId: requestId(), entryVersion: 1, linkVersion: 0, itemId: reserve })
  assert.equal((await api.detail(reserve)).balance.availableAmount, -558000)
  await api.removeLink(paid.id, { requestId: requestId(), entryVersion: 1, linkVersion: 1 }); assert.equal((await api.detail(reserve)).balance.availableAmount, 52000)
  const moved = await ledger(`/entries/${paid.id}`, 'PUT', editBody(paid, { date: '2026-10-31' }))
  assert.equal((await api.execution('2026-10')).unlinkedExpenseTotal, 610000)
  await assert.rejects(api.setLink(paid.id, { requestId: requestId(), entryVersion: moved.version, linkVersion: 2, itemId: reserve }), e => e.status === 422)
  assert.equal((await api.execution(month)).expenseTotal, 0)
})
await check('真实双分页和后续停用保留全量余额、使用记录', async () => {
  // 默认分页 20 条；不依赖插入顺序或 UUID 大小推断记录时间。
  const bills = []
  for (let i = 0; i < 21; i++) {
    const paid = await bill(100); bills.push(paid.id)
    await api.setLink(paid.id, { requestId: requestId(), entryVersion: 1, linkVersion: 0, itemId: reserve })
  }
  const first = await api.detail(reserve), second = await api.detail(reserve, first.nextRecordCursor, first.nextEntryCursor)
  assert.equal(first.entries.length, 20); assert.equal(second.entries.length, 1)
  assert.equal(new Set([...first.entries, ...second.entries].map(row => row.entry.id)).size, 21)
  assert.ok(first.nextRecordCursor); assert.equal(first.balance.availableAmount, 49900); assert.deepEqual(first.balance, second.balance)
  const plan = copy(config.plan); plan.items = plan.items.filter(item => item.id !== reserve)
  await savePlan(api, plan, 2, '2027-11'); assert.ok((await api.projects()).items.some(item => item.id === reserve))
  assert.equal((await api.detail(reserve)).entries.length, 20)
})
await check('共同成员只关联本人账单；跨账本和账号隔离', async () => {
  const sharedId = config.books[1], shared = createBudgetApi(token, sharedId), plan = copy(config.plan)
  const groups = new Map(plan.groups.map(group => [group.id, randomUUID()]))
  plan.groups.forEach(group => { group.id = groups.get(group.id) }); plan.items.forEach(item => { item.id = randomUUID(); item.groupId = groups.get(item.groupId) })
  await savePlan(shared, plan); const own = await bill(1000, date, sharedId, 1), other = await bill(2000, date, sharedId)
  const member = storeFor(1, sharedId, 'shared'), itemId = plan.items[3].id
  assert.ok(await member.load()); const count = requests
  await member.write('fund', itemId, {}); assert.equal(requests, count); assert.match(member.error, /创建者/)
  await member.write('setLink', other.id, {}); assert.equal(requests, count)
  assert.ok(await member.write('setLink', own.id, { requestId: requestId(), entryVersion: 1, linkVersion: 0, itemId }))
  assert.equal((await shared.detail(itemId)).balance.availableAmount, -1000)
  await assert.rejects(createBudgetApi('user-2', sharedId).plan(), e => e.status === 404)
  await assert.rejects(shared.detail(saving), e => e.status === 404)
  store = storeFor(2, config.books[2]); assert.ok(await store.load()); assert.equal(store.month.state, 'not_set')
})
assert.equal(passed, 8)
console.log(`真实 HTTP / PostgreSQL 联调：${passed} 组通过；仅登录令牌与传输地址由隔离测试配置。`)
