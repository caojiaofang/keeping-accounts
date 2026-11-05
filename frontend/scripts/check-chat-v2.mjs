import assert from 'node:assert/strict'
import path from 'node:path'
import { readFile } from 'node:fs/promises'
import { randomUUID } from 'node:crypto'
import { createPinia, setActivePinia } from 'pinia'
import { moduleUrl, root } from './test-modules.mjs'

const { useChatStore } = await import(await moduleUrl(path.join(root, 'store/chat.ts')))
const { useAuthStore } = await import(await moduleUrl(path.join(root, 'store/auth.ts')))
const { useLedgerStore } = await import(await moduleUrl(path.join(root, 'store/ledger.ts')))
const codec = await import(await moduleUrl(path.join(root, 'common/api/chat-v2-codec.ts')))
const { ChatEventParserV2, StreamDecoder, chatStreamV2 } = await import(await moduleUrl(path.join(root, 'common/api/chat-stream.ts')))
const clone = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value))
const users = [1, 2].map(i => ({ id: randomUUID(), openid: `mock-v2-${i}`, created_at: '2026-11-05T00:00:00Z', last_login_at: '2026-11-05T00:00:00Z' }))
const books = users.map(user => [1, 2].map(i => ({ id: randomUUID(), ownerId: user.id, name: `测试账本${i}`, budget: 500000, version: 1, kind: 'personal', icon: 'leaf', color: '#e1eee7', description: '' })))
const storage = new Map(), sessions = new Map(), conversations = new Map()
const BASE = '/api/v1/assistant/v2'
let identity, handler, capability, streamGate, transformFrame, persistMessages, partial, nextContext, aborts, calls, modelCalls, sequence
const ok = data => ({ statusCode: 200, data: clone(data) })
const fail = (statusCode, detail = '模拟服务错误') => ({ statusCode, data: { detail } })
function filters(start = '2026-10-21', end = '2026-10-27') {
  return { dateRange: { start, end }, entryType: 'expense', categoryIds: [], keywords: { include: [], mode: 'any', exclude: [] }, accounts: [], creatorIds: [], amountMin: null, amountMax: null, minInclusive: true, maxInclusive: true, weekdays: [], excludedEntryIds: [] }
}
function context(range = filters()) { return { schemaVersion: 2, filters: range, previousRange: null, sources: [], resultRefs: [], summary: '', summaryThroughSequence: 0 } }
function turnOf(c, body) {
  const seq = c.turns.length + 1, ctx = partial ? c.context : nextContext || context()
  const facts = [
    { id: 'q1.expense.total', value: 1000, unit: 'cents', label: '支出合计', sourceCallId: 'q1', metric: 'total' },
    { id: 'q1.expense.count', value: 1, unit: 'count', label: '支出笔数', sourceCallId: 'q1', metric: 'count' },
  ]
  return { schemaVersion: 2, id: body.id, sequence: seq, text: body.text, answer: '已确认的餐饮🍜合计 ¥10.00。', completeness: partial ? 'partial' : 'complete', missing: partial ? ['比较暂未完成'] : [],
    cards: [{ id: 'q1.expense', type: 'summary', title: '当前支出', filters: ctx.filters, previousRange: null, generatedAt: 1000, factIds: facts.map(f => f.id), rows: [], footnote: '基于当前有效账单' }],
    factRefs: facts, suggestions: partial ? [] : [{ id: randomUUID(), text: '查看匹配账单明细', sourceTurnId: body.id, reference: null }], context: clone(ctx), createdAt: 1000 + sequence++ }
}
async function server(options) {
  calls.push({ path: new URL(options.url).pathname, method: options.method || 'GET', data: clone(options.data), query: new URL(options.url).search })
  const url = new URL(options.url), pathname = url.pathname, token = options.header?.Authorization?.slice(7)
  if (pathname.endsWith('/wechat/login')) {
    const access_token = String.fromCharCode(65 + sequence++).repeat(43)
    sessions.set(access_token, identity)
    return ok({ access_token, token_type: 'bearer', expires_in: 604800, is_new_user: false, user: identity })
  }
  if (!sessions.has(token)) return fail(401, '登录已过期')
  const user = sessions.get(token)
  if (pathname.endsWith('/auth/me')) return ok(user)
  if (pathname.endsWith('/logout')) { sessions.delete(token); return { statusCode: 204, data: '' } }
  if (pathname.endsWith('/ledger')) return ok({ books: books[users.indexOf(user)], entries: [] })
  if (pathname === BASE + '/capabilities') return typeof capability === 'number' ? fail(capability) : ok(capability)
  const legacyPrefix = '/api/v1/assistant/conversations'
  if (pathname === legacyPrefix) return ok([...conversations.values()].filter(c => c.legacy && c.ownerId === user.id && c.bookId === url.searchParams.get('bookId') && c.month === url.searchParams.get('month')))
  if (pathname.startsWith(legacyPrefix + '/')) {
    const c = conversations.get(pathname.slice(legacyPrefix.length + 1).split('/')[0])
    if (!c?.legacy || c.ownerId !== user.id) return fail(404)
    const before = Number(url.searchParams.get('before') || Infinity), rows = c.turns.filter(t => t.sequence < before)
    return ok({ ...c, turns: rows.slice(-20), hasMore: rows.length > 20 })
  }
  if (pathname.startsWith(BASE + '/legacy/conversations/') && pathname.endsWith('/fork')) {
    const source = conversations.get(pathname.split('/').at(-2)), body = options.data
    if (!source?.legacy || source.ownerId !== user.id || source.bookId !== body.bookId) return fail(404)
    if (!conversations.has(body.id)) {
      const old = source.turns.at(-1).context
      conversations.set(body.id, { schemaVersion: 2, ...body, ownerId: user.id, title: '新的消费分析', updatedAt: sequence++, turns: [], context: context({ ...filters(old.month + '-01', old.month + '-27'), entryType: old.entryType, categoryIds: old.categoryId ? [old.categoryId] : [] }), requests: {} })
    }
    return ok(conversations.get(body.id))
  }
  if (!pathname.startsWith(BASE)) return fail(404)
  const prefix = BASE + '/conversations'
  if (pathname === prefix && options.method === 'POST') {
    const body = options.data
    if (!conversations.has(body.id)) conversations.set(body.id, { schemaVersion: 2, ...body, ownerId: user.id, title: '新的消费分析', updatedAt: sequence++, turns: [], hasMore: false, context: context(filters(body.initialMonth + '-01', body.initialMonth + '-27')), requests: {} })
    return ok(conversations.get(body.id))
  }
  if (pathname === prefix) return ok([...conversations.values()].filter(c => c.ownerId === user.id && c.bookId === url.searchParams.get('bookId') && c.turns.length).sort((a, b) => b.updatedAt - a.updatedAt).slice(0, 20))
  const cid = pathname.slice(prefix.length + 1).split('/')[0], c = conversations.get(cid)
  if (!c || c.ownerId !== user.id) return fail(404)
  if (pathname.endsWith('/messages/stream')) {
    const old = c.turns.find(t => t.id === options.data.id)
    if (old) return old.text === options.data.text && (c.requests[old.id]?.suggestionId || null) === (options.data.suggestionId || null) ? ok(old) : fail(409)
    modelCalls++
    const turn = turnOf(c, options.data)
    if (persistMessages) {
      c.turns.push(turn); c.requests[turn.id] = clone(options.data); c.updatedAt = sequence++; c.title = c.turns[0].text
      if (turn.completeness === 'complete') c.context = clone(turn.context)
    }
    return ok(turn)
  }
  const before = Number(url.searchParams.get('before') || Infinity), rows = c.turns.filter(t => t.sequence < before)
  return ok({ ...c, turns: rows.slice(-20), hasMore: rows.length > 20 })
}
globalThis.getCurrentPages = () => [{ route: 'pages/assistant/index' }]
globalThis.uni = {
  getStorageSync: key => clone(storage.get(key)), setStorageSync: (key, value) => storage.set(key, clone(value)), removeStorageSync: key => storage.delete(key),
  showToast() {}, reLaunch() {}, login(options) { options.success({ code: 'mock-v2-code' }) },
  request(options) {
    let listener, aborted = false
    Promise.resolve().then(() => handler(options)).then(async response => {
      if (aborted) return
      if (!options.enableChunked || response.statusCode !== 200) { options.success(response); return }
      function frame(event, data) {
        const value = transformFrame ? transformFrame(event, clone(data)) : data
        if (value === undefined) return
        const bytes = Buffer.from(`event: ${event}\r\ndata: ${JSON.stringify(value)}\r\n\r\n`)
        for (let i = 0; i < bytes.length && !aborted; i += 7) {
          const part = bytes.subarray(i, i + 7)
          listener?.({ data: part.buffer.slice(part.byteOffset, part.byteOffset + part.byteLength) })
        }
      }
      const saved = response.data
      const preview = { schemaVersion: 2, answer: '', completeness: saved.completeness, missing: saved.missing, cards: saved.cards, factRefs: saved.factRefs, suggestions: saved.suggestions }
      frame('status', { text: '正在核对账单…' })
      frame('snapshot', { ...preview, cards: saved.cards.map(c => ({ ...c, title: '将被替换的卡片' })) })
      frame('delta', { text: '临时正文🍵。' })
      frame('snapshot', { ...preview, answer: '完整替换后的预览🍜。' })
      if (streamGate) await streamGate(options)
      if (aborted) return
      frame('done', saved)
      options.success({ statusCode: 200, data: new ArrayBuffer(0) })
    }).catch(() => { if (!aborted) options.fail({ errMsg: 'network unavailable' }) })
    return { onChunkReceived(value) { listener = value }, abort() { aborted = true; aborts++; options.fail({ errMsg: 'abort' }) } }
  },
}
const tick = () => new Promise(resolve => setTimeout(resolve, 0))
async function fresh(cap = { enabled: true, schemaVersions: [2] }) {
  storage.clear(); sessions.clear(); conversations.clear(); identity = users[0]; sequence = 0
  capability = cap; handler = server; streamGate = undefined; transformFrame = undefined; persistMessages = true; partial = false; nextContext = undefined; aborts = 0; calls = []; modelCalls = 0
  setActivePinia(createPinia())
  const auth = useAuthStore(); await auth.bootstrap(); await auth.login()
  const ledger = useLedgerStore(), chat = useChatStore()
  await chat.load('2026-10')
  return { auth, ledger, chat }
}
let passed = 0
async function check(name, action) { await action(); passed++; console.log(`通过：${name}`) }
function seedLegacy() {
  const id = randomUUID()
  conversations.set(id, { id, legacy: true, bookId: books[0][0].id, ownerId: identity.id, month: '2026-10', title: '旧版对话', updatedAt: sequence++, turns: [{ id: randomUUID(), sequence: 1, text: '旧问题', answer: '原始回答', result: { title: '旧统计卡', text: '', total: 1800, count: 1, scope: '2026-09-01 至 2026-09-27', entryType: 'expense', rows: [], footnote: '历史快照' }, context: { month: '2026-09', categoryId: 'food', entryType: 'expense' }, createdAt: 1000 }] })
  return id
}

await check('发送前协商 V2，创建只传入口月份，历史查询不带月份', async () => {
  const { chat } = await fresh()
  assert.equal(chat.protocol, 'v2'); assert.equal(chat.ready, true)
  assert.equal(await chat.send('最近七天花多少'), true)
  const creation = calls.find(c => c.path === BASE + '/conversations' && c.method === 'POST')
  assert.equal(creation.data.initialMonth, '2026-10'); assert.equal(creation.data.month, undefined)
  assert.ok(calls.filter(c => c.path === BASE + '/conversations' && c.method === 'GET').every(c => !c.query.includes('month=')))
  assert.equal(modelCalls, 1); assert.equal(chat.contextV2.filters.dateRange.start, '2026-10-21')
})
await check('只有能力明确关闭或 404 才进入 V1，鉴权/网络/5xx/格式错误均不回退', async () => {
  for (const value of [{ enabled: false, schemaVersions: [2] }, 404]) assert.equal((await fresh(value)).chat.protocol, 'v1')
  for (const value of [401, 403, 503, { enabled: true, schemaVersions: [9] }, { enabled: 'yes', schemaVersions: [2] }]) {
    const { chat } = await fresh(value)
    assert.equal(chat.protocol, 'unknown'); assert.equal(chat.ready, false); assert.equal(await chat.send('不能发送'), false)
    assert.ok(!calls.some(c => /\/assistant\/conversations/.test(c.path)))
  }
  const { chat } = await fresh()
  handler = async options => { if (options.url.endsWith('/v2/capabilities')) throw new Error('offline'); return server(options) }
  await chat.load('2026-10', true)
  assert.equal(chat.protocol, 'unknown'); assert.ok(chat.error)
})
await check('多个快照整体替换卡片/事实/正文，done 才确认并覆盖预览', async () => {
  const { chat } = await fresh()
  let release
  streamGate = () => new Promise(resolve => { release = resolve })
  const task = chat.send('看看')
  await tick()
  assert.equal(chat.turns.length, 0); assert.ok(chat.pending); assert.equal(chat.preview.cards.length, 1)
  assert.equal(chat.preview.cards[0].title, '当前支出'); assert.equal(chat.preview.answer, '完整替换后的预览🍜。')
  release(); assert.equal(await task, true)
  assert.equal(chat.turns[0].answer, '已确认的餐饮🍜合计 ¥10.00。'); assert.equal(chat.preview, undefined)
})
await check('部分完成消息保留缺失说明和成功上下文，不伪造动态建议', async () => {
  const { chat } = await fresh(); await chat.send('先查')
  const saved = clone(chat.contextV2)
  partial = true; nextContext = context(filters('2026-09-01', '2026-09-27'))
  assert.equal(await chat.send('再比较'), true)
  assert.equal(chat.turns.at(-1).completeness, 'partial'); assert.ok(chat.turns.at(-1).missing.length)
  assert.deepEqual(chat.contextV2, saved); assert.deepEqual(chat.suggestions, [])
})
await check('动态建议只发送已保存 ID，断流重试保留消息 ID、原文和建议 ID', async () => {
  const { chat } = await fresh(); await chat.send('汇总')
  const suggestion = clone(chat.suggestions[0]); const before = modelCalls
  streamGate = async () => { throw new Error('done lost') }
  assert.equal(await chat.sendSuggestion(suggestion), false)
  const pending = clone(chat.pending); assert.equal(pending.suggestionId, suggestion.id); assert.ok(chat.preview)
  assert.equal(await chat.send(pending.text), false)
  streamGate = undefined
  assert.equal(await chat.retry(), true)
  assert.equal(modelCalls, before + 1)
  const sent = calls.filter(c => c.path.endsWith('/messages/stream')).slice(-2)
  assert.deepEqual(sent[0].data, sent[1].data); assert.equal(chat.pending, undefined)
})
await check('未保存预览中的建议不能执行，读取未找到消息时仍保留原 pending', async () => {
  const { chat } = await fresh(); persistMessages = false
  streamGate = async () => { throw new Error('not committed') }
  assert.equal(await chat.send('查询'), false)
  const pending = clone(chat.pending), preview = clone(chat.preview)
  assert.equal(await chat.sendSuggestion(preview.suggestions[0]), false)
  assert.equal(await chat.read(chat.conversationId), true)
  assert.deepEqual(chat.pending, pending); assert.deepEqual(chat.preview, preview)
})
await check('重新读取恢复已提交消息并清理未确认预览，不重复生成', async () => {
  const { chat } = await fresh(); streamGate = async () => { throw new Error('done lost') }
  await chat.send('查询'); const count = modelCalls
  assert.equal(await chat.read(chat.conversationId), true)
  assert.equal(chat.pending, undefined); assert.equal(chat.preview, undefined); assert.equal(chat.turns.length, 1); assert.equal(modelCalls, count)
})
await check('历史允许跨入口月份，翻旧页以顶层权威上下文为准', async () => {
  const { chat } = await fresh(); await chat.send('当前对话')
  const c = conversations.get(chat.conversationId)
  c.initialMonth = '2026-09'; c.context = context(filters('2026-11-01', '2026-11-05')); c.updatedAt += 100
  const first = clone(c.turns[0])
  c.turns = Array.from({ length: 25 }, (_, i) => ({ ...clone(first), id: randomUUID(), sequence: i + 1, text: `历史${i}`, suggestions: [], context: context(filters('2026-09-01', '2026-09-27')) }))
  assert.equal(await chat.read(c.id), true); assert.equal(chat.turns.length, 20)
  await chat.loadEarlier(); assert.equal(chat.turns.length, 25)
  assert.equal(chat.contextV2.filters.dateRange.start, '2026-11-01')
  const cid = chat.conversationId; await chat.load('2026-12')
  assert.equal(chat.conversationId, cid); assert.equal(chat.contextV2.filters.dateRange.start, '2026-11-01')
})
await check('幂等重放旧 done 后重新获取权威范围，其他设备的新筛选不会被旧上下文覆盖', async () => {
  const { chat } = await fresh(); streamGate = async () => { throw new Error('done lost') }; await chat.send('旧消息')
  const c = conversations.get(chat.conversationId)
  const other = { ...clone(c.turns[0]), id: randomUUID(), sequence: 2, text: '另一设备', suggestions: [], context: context(filters('2026-11-01', '2026-11-05')) }
  c.turns.push(other); c.context = clone(other.context); c.updatedAt += 100
  streamGate = undefined; assert.equal(await chat.retry(), true)
  assert.equal(chat.contextV2.filters.dateRange.start, '2026-11-01'); assert.equal(chat.turns.length, 2); assert.equal(modelCalls, 1)
})
await check('切到记账模式或离页取消流，迟到 done 不写回且 pending 可恢复', async () => {
  const { chat } = await fresh(); let release
  streamGate = () => new Promise(resolve => { release = resolve })
  const task = chat.send('查询'); await tick(); const pending = clone(chat.pending)
  chat.suspend(); assert.equal(aborts, 1); assert.equal(chat.sending, false); assert.deepEqual(chat.pending, pending)
  release(); assert.equal(await task, false); assert.equal(chat.turns.length, 0)
  assert.equal(await chat.read(chat.conversationId), true); assert.equal(chat.pending, undefined)
})
await check('切换账号或账本清空范围、建议和预览，迟到响应被忽略', async () => {
  for (const switching of ['account', 'book']) {
    const { chat, auth, ledger } = await fresh(); let release
    streamGate = () => new Promise(resolve => { release = resolve })
    const task = chat.send('查询'); await tick()
    if (switching === 'account') auth.enterGuest(); else ledger.selectBook(books[0][1].id)
    release(); assert.equal(await task, false)
    assert.equal(chat.contextV2, undefined); assert.equal(chat.pending, undefined); assert.equal(chat.preview, undefined); assert.equal(chat.protocol, 'unknown'); assert.deepEqual(chat.suggestions, [])
  }
})
await check('V2 发送后即使收到 404 或失权，也不会偷偷改协议重发', async () => {
  for (const status of [404, 403, 503]) {
    const { chat } = await fresh()
    handler = options => options.url.endsWith('/messages/stream') ? fail(status) : server(options)
    assert.equal(await chat.send('查询'), false); assert.equal(chat.protocol, 'v2'); assert.ok(chat.pending)
    assert.ok(!calls.some(c => /\/assistant\/conversations/.test(c.path)))
  }
})
await check('V2 帧校验拒绝未知事实、重复卡片、不完整状态和不安全整数', async () => {
  const c = { turns: [], context: context() }, turn = turnOf(c, { id: randomUUID(), text: '查询' })
  const variants = [
    t => { t.cards[0].factIds.push('q1.unknown') }, t => { t.cards.push(clone(t.cards[0])) },
    t => { t.completeness = 'partial'; t.missing = [] }, t => { t.factRefs[0].value = Number.MAX_SAFE_INTEGER + 1 },
    t => { t.context.filters.dateRange.start = '2026-99-01' }, t => { t.suggestions[0].sourceTurnId = randomUUID() },
  ]
  for (const change of variants) { const bad = clone(turn); change(bad); assert.throws(() => codec.parseTurnV2(bad)) }
  assert.throws(() => codec.decodeEventV2('unexpected', {})); assert.equal(codec.formatFactV2({ unit: 'cents', value: -123456 }), '−¥1,234.56')
})
await check('中文和表情跨字节、CRLF、多快照以及 ping 均可安全解析', async () => {
  const received = [], parser = new ChatEventParserV2(event => received.push(event)), decoder = new StreamDecoder()
  const c = { turns: [], context: context() }, turn = turnOf(c, { id: randomUUID(), text: '查询' })
  const source = `: 心跳\r\n\r\nevent: ping\r\ndata: {}\r\n\r\nevent: snapshot\r\ndata: ${JSON.stringify(turn)}\r\n\r\nevent: delta\r\ndata: {"text":"餐饮🍜"}\r\n\r\n`
  for (const byte of Buffer.from(source)) parser.push(decoder.decode(Uint8Array.of(byte).buffer))
  decoder.decode(new ArrayBuffer(0), true)
  assert.equal(received.length, 2); assert.equal(received[1].data.text, '餐饮🍜')
})
await check('H5 V2 流收到 done 正常恢复，非法 done 不确认原消息', async () => {
  await fresh()
  const original = globalThis.fetch; globalThis.window = {}
  const turn = turnOf({ turns: [], context: context() }, { id: randomUUID(), text: '查询' })
  try {
    for (const valid of [true, false]) {
      globalThis.fetch = async () => ({ ok: true, body: new ReadableStream({ start(controller) {
        const value = clone(turn); if (!valid) value.id = randomUUID()
        controller.enqueue(Buffer.from(`event: done\ndata: ${JSON.stringify(value)}\n\n`)); controller.close()
      } }) })
      const task = chatStreamV2('/test', 'token', { id: turn.id, text: turn.text }, () => {})
      if (valid) assert.equal((await task.promise).id, turn.id); else await assert.rejects(task.promise)
    }
  } finally { delete globalThis.window; globalThis.fetch = original }
})
await check('后端 Pydantic 规范化示例可直接被前端解码与展示', async () => {
  const fixture = JSON.parse(await readFile(new URL('./fixtures/chat-v2-turn.json', import.meta.url), 'utf8'))
  const turn = codec.parseTurnV2(fixture)
  assert.equal(turn.factRefs[0].value, 2050); assert.equal(codec.formatFactV2(turn.factRefs[0]), '¥20.50')
  assert.equal(codec.decodeEventV2('done', fixture, fixture.id).data.id, fixture.id)
})
await check('旧请求的迟到能力响应不能为退出账号重新选择协议', async () => {
  const { chat, auth } = await fresh(); let release
  handler = async options => {
    const result = await server(options)
    if (options.url.endsWith('/v2/capabilities')) await new Promise(resolve => { release = resolve })
    return result
  }
  const task = chat.load('2026-10', true); await tick(); auth.enterGuest(); release(); await task
  assert.equal(chat.protocol, 'unknown'); assert.equal(chat.ready, false); assert.equal(chat.ownerId, null)
})
await check('done 后权威范围读取失败会保留已保存结果，并阻止带不确定范围继续发送', async () => {
  const { chat } = await fresh()
  handler = options => /\/conversations\/[a-f0-9-]+$/.test(new URL(options.url).pathname) && options.method !== 'POST' ? fail(503) : server(options)
  assert.equal(await chat.send('查询'), true); assert.equal(chat.turns.length, 1); assert.equal(chat.pending, undefined)
  assert.equal(chat.ready, false); assert.equal(await chat.send('不能继续猜条件'), false)
  handler = server; assert.equal(await chat.read(chat.conversationId), true); assert.equal(chat.ready, true)
})
await check('其他设备产生超过一页的新消息时，恢复连续分页而不遗失中间历史', async () => {
  const { chat } = await fresh(); streamGate = async () => { throw new Error('done lost') }; await chat.send('旧消息')
  const c = conversations.get(chat.conversationId), original = clone(c.turns[0])
  for (let i = 2; i <= 26; i++) c.turns.push({ ...clone(original), id: randomUUID(), sequence: i, suggestions: [], text: `其他消息${i}` })
  c.updatedAt += 100
  streamGate = undefined; assert.equal(await chat.retry(), true)
  assert.equal(chat.turns[0].sequence, 7); assert.equal(chat.hasMore, true)
  await chat.loadEarlier()
  assert.deepEqual(chat.turns.map(turn => turn.sequence), Array.from({ length: 26 }, (_, i) => i + 1))
})
await check('V2 启用时仍可只读查看旧版原始统计卡，显式新建后继承旧筛选续聊', async () => {
  const { chat } = await fresh(), source = seedLegacy()
  assert.equal(await chat.refreshLegacyHistory(), true); assert.equal(chat.legacyHistory[0].id, source)
  assert.equal(await chat.readLegacy(source), true); assert.equal(chat.legacyReadonly, true)
  assert.equal(chat.turns[0].result.total, 1800); assert.equal(await chat.send('不能直接写旧会话'), false)
  assert.equal(await chat.startFromLegacy(), true); assert.equal(chat.legacyReadonly, false)
  assert.notEqual(chat.conversationId, source); assert.deepEqual(chat.contextV2.filters.categoryIds, ['food'])
  assert.equal(chat.contextV2.filters.dateRange.start, '2026-09-01'); assert.equal(chat.turns.length, 0)
  assert.equal(conversations.get(source).turns[0].result.total, 1800); assert.equal(modelCalls, 0)
})
await check('旧会话新建续聊响应丢失后重试复用目标会话 ID，不额外生成会话', async () => {
  const { chat } = await fresh(), source = seedLegacy(); await chat.readLegacy(source)
  handler = async options => { const result = await server(options); if (options.url.endsWith('/fork')) throw new Error('response lost'); return result }
  assert.equal(await chat.startFromLegacy(), false); assert.equal(chat.legacyReadonly, true)
  const first = calls.find(c => c.path.endsWith('/fork')).data.id
  await chat.load('2026-11')
  handler = server; assert.equal(await chat.startFromLegacy(), true); assert.equal(chat.conversationId, first)
  assert.ok(calls.filter(c => c.path.endsWith('/fork')).every(c => c.data.initialMonth === '2026-10'))
  assert.equal(conversations.size, 2); assert.equal(modelCalls, 0)
})
await check('旧版历史的迟到读取不能写回切换后的账本', async () => {
  const { chat, ledger } = await fresh(), source = seedLegacy(); let release
  handler = async options => { const result = await server(options); if (options.url.endsWith('/' + source)) await new Promise(resolve => { release = resolve }); return result }
  const task = chat.readLegacy(source); await tick(); ledger.selectBook(books[0][1].id); release()
  assert.equal(await task, false); assert.equal(chat.legacyReadonly, false); assert.deepEqual(chat.turns, [])
})
console.log(`V2 聊天前端检查完成：${passed} 项通过；全部使用模拟服务与内存数据。`)
