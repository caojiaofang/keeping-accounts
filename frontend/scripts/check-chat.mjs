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
let identity = users[0], sequence = 0, modelCalls = 0, handler, streamGate, aborts = 0
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
  if (/\/messages(?:\/stream)?$/.test(pathname)) {
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
  request(options) {
    let receive, aborted = false
    Promise.resolve().then(() => handler(options)).then(async result => {
      if (aborted) return
      if (!options.enableChunked || result.statusCode !== 200) { options.success(result); return }
      assert.equal(options.timeout, 300000)
      const frame = (event, data) => {
        const bytes = Buffer.from(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
        // 故意拆开中文 UTF-8 字节、JSON 转义和 SSE 帧边界。
        for (let i = 0; i < bytes.length; i += 7) {
          const part = bytes.subarray(i, i + 7)
          receive?.({ data: part.buffer.slice(part.byteOffset, part.byteOffset + part.byteLength) })
        }
      }
      frame('status', { text: '正在补充分析…' })
      frame('snapshot', { answer: '统计已完成。', result: result.data.result })
      frame('delta', { text: '\n\n先核对餐饮记录。' })
      if (streamGate) await streamGate(options)
      if (aborted) return
      frame('done', result.data)
      options.success({ statusCode: 200, data: new ArrayBuffer(0) })
    }).catch(() => { if (!aborted) options.fail({}) })
    return { onChunkReceived(listener) { receive = listener }, abort() { aborted = true; aborts++; options.fail({}) } }
  },
}
const tick = () => new Promise(resolve => setTimeout(resolve, 0))
async function fresh() {
  storage.clear(); sessions.clear(); conversations.clear(); identity = users[0]; sequence = 0; modelCalls = 0; handler = server; streamGate = undefined; aborts = 0
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
    if (/\/messages(?:\/stream)?$/.test(new URL(options.url).pathname)) { lostId = options.data.id; throw new Error('response lost') }
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
    if (/\/messages(?:\/stream)?$/.test(new URL(options.url).pathname)) await new Promise(resolve => { release = resolve })
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

await check('流式统计和正文在结束前展示，收到 done 才转入正式历史', async () => {
  const { chat } = await fresh()
  let release
  streamGate = () => new Promise(resolve => { release = resolve })
  const sending = chat.send('看看餐饮')
  await tick()
  assert.equal(chat.sending, true)
  assert.equal(chat.turns.length, 0)
  assert.equal(chat.preview.result.total, 2050)
  assert.equal(chat.preview.answer, '统计已完成。\n\n先核对餐饮记录。')
  assert.equal(chat.progress, '正在补充分析…')
  release()
  assert.equal(await sending, true)
  assert.equal(chat.preview, undefined)
  assert.equal(chat.turns.length, 1)
})

await check('已有流式预览时切换账号会取消连接，迟到的分片不会恢复预览', async () => {
  const { chat, auth } = await fresh()
  let release
  streamGate = () => new Promise(resolve => { release = resolve })
  const sending = chat.send('看看餐饮')
  await tick()
  assert.ok(chat.preview)
  auth.enterGuest()
  assert.equal(aborts, 1)
  assert.equal(chat.preview, undefined)
  release()
  assert.equal(await sending, false)
  assert.equal(chat.turns.length, 0)
})

await check('断流保留未确认预览及原消息ID，重新读取恢复正式答案', async () => {
  const { chat } = await fresh()
  streamGate = async () => { throw new Error('网络中断') }
  assert.equal(await chat.send('看看餐饮'), false)
  assert.ok(chat.preview)
  assert.ok(chat.pending)
  assert.equal(chat.turns.length, 0)
  assert.ok(chat.error)
  assert.equal(await chat.read(chat.conversationId), true)
  assert.equal(chat.preview, undefined)
  assert.equal(chat.pending, undefined)
  assert.equal(chat.turns.length, 1)
})

await check('UTF-8 中文和表情跨字节解码，SSE 多帧及 CRLF 任意拆分保持完整', async () => {
  const { StreamDecoder, ChatEventParser } = await import(await moduleUrl(path.join(root, 'common/api/chat-stream.ts')))
  const decoder = new StreamDecoder(), events = []
  const parser = new ChatEventParser(event => events.push(event))
  const source = ': 心跳\r\n\r\nevent: ping\r\ndata: {}\r\n\r\nevent: delta\r\ndata: {"text":"餐饮🍜\\n记录"}\r\n\r\nevent: status\ndata: {"text":"继续"}\n\n'
  for (const byte of Buffer.from(source)) parser.push(decoder.decode(Uint8Array.of(byte).buffer))
  decoder.decode(new ArrayBuffer(0), true)
  assert.deepEqual(events, [{ event: 'delta', data: { text: '餐饮🍜\n记录' } }, { event: 'status', data: { text: '继续' } }])
  const broken = new StreamDecoder()
  broken.decode(Uint8Array.of(0xe4).buffer)
  assert.throws(() => broken.decode(new ArrayBuffer(0), true), /中断/)
})

await check('H5 使用 fetch 读取真实分片并在完成前展示正文', async () => {
  const { chatStream } = await import(await moduleUrl(path.join(root, 'common/api/chat-stream.ts')))
  const originalFetch = globalThis.fetch
  let controller, readerCancelled = false
  globalThis.window = {}
  globalThis.fetch = async (_url, options) => {
    assert.equal(options.method, 'POST')
    assert.equal(options.headers.Authorization, 'Bearer token')
    return { ok: true, body: new ReadableStream({ start(value) { controller = value }, cancel() { readerCancelled = true } }) }
  }
  try {
    const events = [], task = chatStream('/test', 'token', { text: '看看' }, event => events.push(event))
    await tick()
    controller.enqueue(Buffer.from('event: delta\ndata: {"text":"中文增量"}\n\n'))
    await tick()
    assert.equal(events[0].data.text, '中文增量')
    controller.enqueue(Buffer.from('event: done\ndata: {"id":"done"}\n\n'))
    controller.close()
    assert.equal((await task.promise).id, 'done')
    assert.equal(readerCancelled, false)
  } finally { delete globalThis.window; globalThis.fetch = originalFetch }
})

console.log(`真实聊天前端检查完成：${passed} 项通过；只使用模拟服务，不发送模型请求。`)
