import assert from 'node:assert/strict'
import path from 'node:path'
import { createPinia, setActivePinia } from 'pinia'
import { moduleUrl, root } from './test-modules.mjs'

const { useChatStore } = await import(await moduleUrl(path.join(root, 'store/chat.ts')))
const { useAuthStore } = await import(await moduleUrl(path.join(root, 'store/auth.ts')))
const { useLedgerStore } = await import(await moduleUrl(path.join(root, 'store/ledger.ts')))
const clone = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value))
const storage = new Map(), sessions = new Map(), conversations = new Map()
const users = ['1', '2'].map(n => ({ id: `${n.repeat(8)}-${n.repeat(4)}-4${n.repeat(3)}-8${n.repeat(3)}-${n.repeat(12)}`, openid: 'mock-' + n, created_at: '2026-10-24T00:00:00Z', last_login_at: '2026-10-24T00:00:00Z' }))
let identity = users[0], sequence = 0, modelCalls = 0, handler
const book = user => ({ id: 'book-' + user.id, name: '日常账本', budget: 500000, version: 1, kind: 'personal', icon: 'leaf', color: '#e1eee7', description: '' })
const fail = (statusCode, detail) => ({ statusCode, data: { detail } })
const ok = data => ({ statusCode: 200, data: clone(data) })

async function server(options) {
  const url = new URL(options.url), pathname = url.pathname
  const token = options.header.Authorization?.slice(7)
  if (pathname.endsWith('/wechat/login')) {
    const access_token = String.fromCharCode(65 + sequence++).repeat(43)
    sessions.set(access_token, clone(identity))
    return ok({ access_token, token_type: 'bearer', expires_in: 604800, is_new_user: false, user: identity })
  }
  if (!sessions.has(token)) return fail(401, '登录已过期')
  const user = sessions.get(token)
  if (pathname.endsWith('/auth/me')) return ok(user)
  if (pathname.endsWith('/logout')) { sessions.delete(token); return { statusCode: 204, data: '' } }
  if (pathname.endsWith('/ledger')) return ok({ books: [book(user)], entries: [] })
  const prefix = '/api/v1/assistant/conversations'
  if (pathname === prefix && options.method === 'POST') {
    const old = conversations.get(options.data.id)
    if (!old) conversations.set(options.data.id, { ...options.data, ownerId: user.id, turns: [], title: '新的消费分析', updatedAt: 0 })
    return ok(conversations.get(options.data.id))
  }
  if (pathname === prefix) return ok([...conversations.values()].filter(c => c.ownerId === user.id && c.bookId === url.searchParams.get('bookId') && c.month === url.searchParams.get('month') && c.turns.length).sort((a, b) => b.updatedAt - a.updatedAt))
  const id = pathname.slice(prefix.length + 1).split('/')[0]
  const conversation = conversations.get(id)
  if (!conversation || conversation.ownerId !== user.id) return fail(404, '会话不存在')
  if (pathname.endsWith('/messages')) {
    const existing = conversation.turns.find(t => t.id === options.data.id)
    if (existing) return existing.text === options.data.text ? ok(existing) : fail(409, '此消息已处理')
    modelCalls++
    const turn = { id: options.data.id, sequence: conversation.turns.length + 1, text: options.data.text, answer: '共记录两笔餐饮，具体金额以卡片为准。', context: { month: conversation.month, categoryId: 'food', entryType: 'expense' }, result: { title: '餐饮支出', text: '', total: 2050, count: 2, scope: '2026-10-01 至 2026-10-24', rows: [], footnote: '后端计算' }, createdAt: 1000 + sequence++ }
    conversation.turns.push(turn)
    conversation.title = conversation.turns[0].text
    conversation.updatedAt = sequence++
    return ok(turn)
  }
  const before = Number(url.searchParams.get('before') || Infinity)
  const eligible = conversation.turns.filter(t => t.sequence < before)
  return ok({ ...conversation, turns: eligible.slice(-20), hasMore: eligible.length > 20 })
}

globalThis.getCurrentPages = () => [{ route: 'pages/assistant/index' }]
globalThis.uni = {
  getStorageSync: key => clone(storage.get(key)), setStorageSync: (key, value) => storage.set(key, clone(value)), removeStorageSync: key => storage.delete(key),
  showToast() {}, reLaunch() {}, login(options) { options.success({ code: 'mock-code' }) },
  request(options) { Promise.resolve().then(() => handler(options)).then(result => options.success(result), () => options.fail({})) },
}
const tick = () => new Promise(resolve => setTimeout(resolve, 0))
async function fresh() {
  storage.clear(); sessions.clear(); conversations.clear(); identity = users[0]; sequence = 0; modelCalls = 0; handler = server
  setActivePinia(createPinia())
  const auth = useAuthStore(); await auth.bootstrap(); await auth.login()
  const ledger = useLedgerStore(), chat = useChatStore()
  await chat.load('2026-10')
  return { auth, ledger, chat }
}
let passed = 0
async function check(name, fn) { await fn(); passed++; console.log(`通过：${name}`) }

await check('登录后自由提问，响应卡片来自服务端，对话不会写入账单', async () => {
  const { chat, ledger } = await fresh()
  assert.equal(await chat.send('餐饮具体花在哪了？'), true)
  assert.equal(chat.turns[0].result.total, 2050)
  assert.equal(chat.context.categoryId, 'food')
  assert.equal(chat.pending, undefined)
  assert.equal(ledger.entries.length, 0)
  assert.equal(chat.history.length, 1)
})

await check('并发点击只发送一次，回复丢失后重试复用消息ID且不重复生成', async () => {
  const { chat } = await fresh()
  let lostId
  handler = async options => {
    const response = await server(options)
    if (new URL(options.url).pathname.endsWith('/messages')) { lostId = options.data.id; throw new Error('response lost') }
    return response
  }
  assert.deepEqual(await Promise.all([chat.send('餐饮呢'), chat.send('餐饮呢')]), [false, false])
  assert.equal(chat.turns.length, 0)
  assert.equal(chat.pending.id, lostId)
  assert.equal(modelCalls, 1)
  handler = server
  assert.equal(await chat.send(chat.pending.text), true)
  assert.equal(chat.turns.length, 1)
  assert.equal(modelCalls, 1)
})

await check('重新读取恢复已保存回答和上下文，新对话保留历史', async () => {
  const { chat } = await fresh()
  await chat.send('餐饮明细')
  const oldId = chat.conversationId
  chat.startNew()
  assert.equal(chat.turns.length, 0)
  await chat.send('收入呢')
  assert.notEqual(chat.conversationId, oldId)
  assert.equal(chat.history.length, 2)
  await chat.read(oldId)
  assert.equal(chat.turns[0].text, '餐饮明细')
  await chat.load('2026-10')
  assert.equal(chat.turns[0].text, '收入呢')
})

await check('历史对话分页加载更早记录，顺序和最新上下文保持正确', async () => {
  const { chat } = await fresh()
  await chat.send('餐饮明细')
  const saved = conversations.get(chat.conversationId)
  saved.turns = Array.from({ length: 25 }, (_, i) => ({ ...saved.turns[0], id: 'history-' + i, sequence: i + 1 }))
  await chat.read(chat.conversationId)
  assert.equal(chat.turns.length, 20)
  assert.equal(chat.hasMore, true)
  await chat.loadEarlier()
  assert.deepEqual(chat.turns.map(t => t.sequence), Array.from({ length: 25 }, (_, i) => i + 1))
  assert.equal(chat.hasMore, false)
})

await check('账号切换立即清空对话，迟到的分析响应不能写入新账号', async () => {
  const { auth, chat } = await fresh()
  let release
  handler = async options => {
    const result = await server(options)
    if (new URL(options.url).pathname.endsWith('/messages')) await new Promise(resolve => { release = resolve })
    return result
  }
  const sending = chat.send('餐饮明细')
  await tick()
  assert.equal(typeof release, 'function')
  auth.enterGuest()
  assert.equal(chat.turns.length, 0)
  assert.equal(chat.pending, undefined)
  release()
  assert.equal(await sending, false)
  handler = server
  identity = users[1]; await auth.login(); await chat.load('2026-10')
  assert.equal(chat.history.length, 0)
  assert.equal(chat.turns.length, 0)
})

await check('历史读取失败不伪造回答，可以保留同一会话重新读取', async () => {
  const { chat } = await fresh()
  await chat.send('看看支出')
  const id = chat.conversationId
  handler = async () => { throw new Error('offline') }
  assert.equal(await chat.read(id), false)
  assert.ok(chat.error)
  handler = server
  assert.equal(await chat.read(id), true)
  assert.equal(chat.turns.length, 1)
})

console.log(`真实聊天前端检查完成：${passed} 项通过；只使用模拟服务，不发送模型请求。`)
