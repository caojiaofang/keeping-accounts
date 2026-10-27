import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createPinia, setActivePinia } from 'pinia'
import ts from 'typescript'

const root = fileURLToPath(new URL('../', import.meta.url))
const compiled = new Map()
async function moduleUrl(filename) {
  if (compiled.has(filename)) return compiled.get(filename)
  let source = ts.transpileModule(
    await readFile(filename, 'utf8'),
    { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } },
  ).outputText
  for (const match of [...source.matchAll(/from\s+(['"])([^'"]+)\1/g)]) {
    const specifier = match[2]
    const local = specifier.startsWith('@/') ? path.join(root, specifier.slice(2)) : specifier.startsWith('.') ? path.resolve(path.dirname(filename), specifier) : null
    const resolved = local ? await moduleUrl(local.endsWith('.ts') ? local : `${local}.ts`) : import.meta.resolve(specifier)
    source = source.replace(match[0], `from '${resolved}'`)
  }
  const url = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
  compiled.set(filename, url)
  return url
}

const { useAuthStore, AUTH_STORAGE_KEY } = await import(await moduleUrl(path.join(root, 'store/auth.ts')))
const { useLedgerStore } = await import(await moduleUrl(path.join(root, 'store/ledger.ts')))
const { apiRequest } = await import(await moduleUrl(path.join(root, 'common/api/request.ts')))
const { safeLoginRedirect } = await import(await moduleUrl(path.join(root, 'common/services/auth.ts')))
const clone = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value))
const storage = new Map()
const sessions = new Map()
const cloudStates = new Map()
const users = ['1', '2'].map(number => ({ id: `${number.repeat(8)}-${number.repeat(4)}-4${number.repeat(3)}-8${number.repeat(3)}-${number.repeat(12)}`, openid: `test-user-${number}`, created_at: '2026-10-24T00:00:00Z', last_login_at: '2026-10-24T00:00:00Z' }))
let identity = users[0]
let loginCount = 0
let sequence = 0
let route = 'pages/login/index'
let handler
let storageFailure = false
const codeHistory = []

async function server(options) {
  const pathname = new URL(options.url).pathname
  const token = options.header.Authorization?.slice(7)
  if (pathname.endsWith('/wechat/login')) {
    codeHistory.push(options.data.code)
    const access_token = String.fromCharCode(65 + sequence++).repeat(43)
    sessions.set(access_token, clone(identity))
    return { statusCode: 200, data: { access_token, token_type: 'bearer', expires_in: 604800, is_new_user: false, user: clone(identity) } }
  }
  if (pathname.endsWith('/logout')) { sessions.delete(token); return { statusCode: 204, data: '' } }
  if (!sessions.has(token)) return { statusCode: 401, data: { detail: '登录已过期，请重新登录' } }
  const user = sessions.get(token)
  if (!cloudStates.has(user.id)) cloudStates.set(user.id, { books: [{ id: `daily-${user.id}`, name: '日常账本', budget: 500000, version: 1, kind: 'personal', icon: 'leaf', color: '#e1eee7', description: '' }], entries: [] })
  const state = cloudStates.get(user.id)
  if (pathname === '/api/v1/ledger') return { statusCode: 200, data: clone(state) }
  if (pathname === '/api/v1/books') {
    let book = state.books.find(item => item.id === options.data.id)
    if (!book) {
      book = { ...state.books[0], id: options.data.id, name: options.data.name, kind: options.data.kind || 'personal', ownerId: user.id, currentUserId: user.id,
        members: options.data.kind === 'shared' ? [{ userId: user.id, name: options.data.memberName || '创建者', role: 'owner', status: 'active', color: '#dcece1' }] : [] }
      state.books.push(book)
    }
    return { statusCode: 200, data: clone(book) }
  }
  if (pathname.endsWith('/budget')) {
    const book = state.books.find(item => item.id === pathname.split('/')[4])
    if (!book) return { statusCode: 404, data: { detail: '无权访问' } }
    if (book.version !== options.data.version) return { statusCode: 409, data: { detail: '账本已在其他设备更新，请刷新后重试' } }
    book.budget = options.data.budget; book.version++
    return { statusCode: 200, data: clone(book) }
  }
  if (pathname === '/api/v1/entries' && options.method === 'POST') {
    let entry = state.entries.find(item => item.id === options.data.id)
    if (!entry) { entry = { ...options.data, version: 1, createdAt: Date.now(), createdBy: user.id }; state.entries.push(entry) }
    return { statusCode: 200, data: clone(entry) }
  }
  if (pathname.startsWith('/api/v1/entries/')) {
    const id = pathname.split('/').at(-1)
    const index = state.entries.findIndex(item => item.id === id)
    if (index < 0) return { statusCode: 404, data: { detail: '账单不存在' } }
    if (options.method === 'DELETE') {
      if (state.entries[index].version !== Number(new URL(options.url).searchParams.get('version'))) return { statusCode: 409, data: { detail: '账单已在其他设备更新，请刷新后重试' } }
      state.entries.splice(index, 1); return { statusCode: 204, data: '' }
    }
    if (state.entries[index].version !== options.data.version) return { statusCode: 409, data: { detail: '账单已在其他设备更新，请刷新后重试' } }
    state.entries[index] = { ...state.entries[index], ...options.data, version: options.data.version + 1 }
    return { statusCode: 200, data: clone(state.entries[index]) }
  }
  return sessions.has(token) ? { statusCode: 200, data: clone(sessions.get(token)) } : { statusCode: 401, data: { detail: '登录已过期，请重新登录' } }
}

globalThis.getCurrentPages = () => [{ route }]
globalThis.uni = {
  getStorageSync: key => clone(storage.get(key)),
  setStorageSync(key, value) { if (storageFailure && key === AUTH_STORAGE_KEY) throw new Error('full'); storage.set(key, clone(value)) },
  removeStorageSync: key => storage.delete(key),
  showToast() {},
  reLaunch({ url }) { route = url.slice(1).split('?')[0] },
  login(options) { loginCount++; options.success({ code: `fresh-code-${loginCount}` }) },
  request(options) { Promise.resolve().then(() => handler(options)).then(result => options.success(result), () => options.fail({ errMsg: 'request:fail' })) },
}
const tick = () => new Promise(resolve => setTimeout(resolve, 0))
async function fresh(clear = true) {
  if (clear) { storage.clear(); sessions.clear(); cloudStates.clear(); loginCount = 0; sequence = 0; codeHistory.length = 0 }
  storageFailure = false
  identity = users[0]
  handler = server
  route = 'pages/login/index'
  setActivePinia(createPinia())
  const auth = useAuthStore()
  const ledger = useLedgerStore()
  await auth.bootstrap()
  return { auth, ledger }
}
let passed = 0
async function check(name, action) { await action(); passed++; console.log(`通过：${name}`) }

await check('首次打开注册登录页，体验数据保留在原存储区', async () => {
  const pages = JSON.parse(await readFile(path.join(root, 'pages.json'), 'utf8'))
  assert.equal(pages.pages[0].path, 'pages/login/index')
  const { auth, ledger } = await fresh()
  assert.equal(auth.user, null)
  assert.ok(ledger.entries.length)
  assert.ok(storage.has('moji-demo-v1'))
  assert.equal(safeLoginRedirect('https://invalid.example'), '/pages/ledger/index')
  assert.equal(safeLoginRedirect('/pages/profile/index'), '/pages/profile/index')
  assert.equal(safeLoginRedirect('%2Fpages%2Fprofile%2Findex'), '/pages/profile/index')
  assert.equal(safeLoginRedirect('%2Fpages%2Fassistant%2Findex'), '/pages/assistant/index')
  assert.equal(safeLoginRedirect('%invalid'), '/pages/ledger/index')
})

await check('真实接口契约与单次登录请求，不把演示账单带入账号', async () => {
  const { auth, ledger } = await fresh()
  const guest = clone(storage.get('moji-demo-v1'))
  await Promise.all([auth.login(), auth.login()])
  assert.equal(loginCount, 1)
  assert.equal(codeHistory.length, 1)
  assert.equal(auth.user.id, users[0].id)
  assert.equal(ledger.accountId, users[0].id)
  assert.equal(ledger.entries.length, 0)
  assert.equal(ledger.books.length, 1)
  assert.deepEqual(storage.get('moji-demo-v1'), guest)
})

await check('退出、切换用户与重新启动分别恢复各自的记录和偏好', async () => {
  const { auth, ledger } = await fresh()
  const guest = clone(storage.get('moji-demo-v1'))
  await auth.login()
  await ledger.saveEntry({ bookId: ledger.activeBookId, amount: 800, categoryId: 'food', date: '2026-10-24', type: 'expense', note: '账号自己的早餐', account: '微信' })
  storage.set(ledger.preferenceKey('nickname'), '甲的称呼')
  await auth.logout()
  assert.deepEqual(storage.get('moji-demo-v1'), guest)
  assert.equal(ledger.accountId, null)
  identity = users[1]
  await auth.login()
  assert.equal(ledger.entries.length, 0)
  assert.equal(storage.get(ledger.preferenceKey('nickname')), undefined)
  identity = users[0]
  await auth.login()
  assert.equal(ledger.entries[0].amount, 800)
  assert.equal(storage.get(ledger.preferenceKey('nickname')), '甲的称呼')
  const restarted = await fresh(false)
  assert.equal(restarted.auth.user.id, users[0].id)
  assert.equal(restarted.ledger.entries[0].amount, 800)
})

await check('401 清理失效凭证与当前账号内存，保留各账号持久化数据', async () => {
  const { auth, ledger } = await fresh()
  await auth.login()
  const key = ledger.preferenceKey('ledger')
  sessions.clear()
  route = 'pages/profile/index'
  await auth.restore()
  assert.equal(auth.user, null)
  assert.equal(ledger.accountId, null)
  assert.equal(storage.has(AUTH_STORAGE_KEY), false)
  assert.ok(storage.has(key))
  assert.equal(route, 'pages/login/index')
})

await check('网络失败保留已有账号与凭证，退出失败可以重试', async () => {
  const { auth, ledger } = await fresh()
  await auth.login()
  handler = async () => { throw new Error('offline') }
  await auth.restore()
  assert.equal(auth.user.id, users[0].id)
  assert.equal(ledger.accountId, users[0].id)
  assert.ok(storage.has(AUTH_STORAGE_KEY))
  await assert.rejects(auth.logout(), /连接不到服务/)
  assert.equal(auth.user.id, users[0].id)
})

await check('身份恢复返回另一用户时拒绝恢复，并清理旧账号内存', async () => {
  const { auth, ledger } = await fresh()
  await auth.login()
  handler = async () => ({ statusCode: 200, data: users[1] })
  await auth.restore()
  assert.equal(auth.user, null)
  assert.equal(ledger.accountId, null)
  assert.match(auth.error, /校验失败/)
})

await check('每次失败重试都重新取得 code，无凭证登录失败不会伪造用户', async () => {
  const { auth } = await fresh()
  handler = async options => {
    codeHistory.push(options.data.code)
    return { statusCode: 401, data: { detail: 'code 无效，请重新尝试' } }
  }
  await auth.login()
  assert.equal(auth.user, null)
  assert.equal(storage.has(AUTH_STORAGE_KEY), false)
  handler = server
  await auth.login()
  assert.equal(auth.user.id, users[0].id)
  assert.notEqual(codeHistory[0], codeHistory[1])
})

await check('旧请求的迟到 401 不会清理新账号登录态', async () => {
  const { auth } = await fresh()
  await auth.login()
  const oldToken = storage.get(AUTH_STORAGE_KEY).accessToken
  identity = users[1]
  await auth.login()
  await tick()
  await assert.rejects(apiRequest('/api/v1/auth/me', { token: oldToken }))
  assert.equal(auth.user.id, users[1].id)
})

await check('过期或残缺的本地凭证不会恢复为登录用户', async () => {
  await fresh()
  storage.set(AUTH_STORAGE_KEY, { accessToken: 'a'.repeat(43), expiresAt: Date.now() - 1, user: users[0] })
  const { auth, ledger } = await fresh(false)
  assert.equal(auth.user, null)
  assert.equal(ledger.accountId, null)
  assert.equal(storage.has(AUTH_STORAGE_KEY), false)
})

await check('存储失败不宣称登录成功，并注销已签发的 token', async () => {
  const { auth } = await fresh()
  storageFailure = true
  await auth.login()
  await tick()
  assert.equal(auth.user, null)
  assert.match(auth.error, /本地空间不足/)
  assert.equal(sessions.size, 0)
})

const inputEntry = ledger => ({ bookId: ledger.activeBookId, amount: 800, categoryId: 'food', date: '2026-10-24', type: 'expense', note: '早餐', account: '微信' })

await check('云端新增、编辑、删除、预算和新账本保存后同步更新明细及统计', async () => {
  const { auth, ledger } = await fresh()
  await auth.login()
  assert.equal(ledger.cloudReady, true)
  assert.equal(await ledger.saveEntry(inputEntry(ledger)), true)
  assert.equal(ledger.expense, 800)
  const saved = clone(ledger.entries[0])
  assert.equal(await ledger.saveEntry({ ...inputEntry(ledger), amount: 1250 }, saved.id), true)
  assert.equal(ledger.expense, 1250)
  assert.equal(await ledger.updateBudget(200000), true)
  assert.equal(ledger.activeBook.budget, 200000)
  assert.equal(await ledger.deleteEntry(saved.id), true)
  assert.equal(ledger.expense, 0)
  const id = await ledger.addBook('旅行')
  assert.ok(ledger.books.some(book => book.id === id && book.name === '旅行'))
  const restarted = await fresh(false)
  assert.ok(restarted.ledger.books.some(book => book.id === id))
})

await check('表单保留打开时的版本，刷新后仍拒绝用旧内容覆盖、删除或更新预算', async () => {
  const { auth, ledger } = await fresh()
  await auth.login()
  await ledger.saveEntry(inputEntry(ledger))
  const old = clone(ledger.entries[0])
  const oldBookVersion = ledger.activeBook.version
  const remote = cloudStates.get(users[0].id)
  remote.entries[0].amount = 2000
  remote.entries[0].version++
  remote.books[0].version++
  await ledger.refreshCloud()
  assert.equal(await ledger.saveEntry({ ...inputEntry(ledger), version: old.version }, old.id), false)
  assert.equal(await ledger.deleteEntry(old.id, old.version), false)
  assert.equal(await ledger.updateBudget(10000, oldBookVersion), false)
  assert.equal(ledger.entries[0].amount, 2000)
})

await check('云端保存失败时不写入本地；已落库但响应丢失后重试不会重复记账', async () => {
  const { auth, ledger } = await fresh()
  await auth.login()
  const entry = inputEntry(ledger)
  let sentId
  handler = async options => {
    sentId = options.data.id
    await server(options)
    throw new Error('response lost')
  }
  assert.equal(await ledger.saveEntry(entry), false)
  assert.equal(ledger.entries.length, 0)
  assert.equal(cloudStates.get(users[0].id).entries.length, 1)
  handler = async options => { assert.equal(options.data.id, sentId); return server(options) }
  assert.equal(await ledger.saveEntry(entry), true)
  assert.equal(ledger.entries.length, 1)
  assert.equal(cloudStates.get(users[0].id).entries.length, 1)
})

await check('迟到的刷新结果不会覆盖刚保存成功的账单', async () => {
  const { auth, ledger } = await fresh()
  await auth.login()
  let release
  handler = async options => {
    const response = await server(options)
    if (new URL(options.url).pathname.endsWith('/ledger')) await new Promise(resolve => { release = () => resolve(response) })
    return response
  }
  const refreshing = ledger.refreshCloud()
  await tick()
  assert.equal(await ledger.saveEntry(inputEntry(ledger)), true)
  release()
  assert.equal(await refreshing, false)
  assert.equal(ledger.entries.length, 1)
})

await check('退出期间的迟到保存响应不会写入体验数据', async () => {
  const { auth, ledger } = await fresh()
  await auth.login()
  const guest = clone(storage.get('moji-demo-v1'))
  let release
  handler = async options => {
    const response = await server(options)
    await new Promise(resolve => { release = () => resolve(response) })
    return response
  }
  const saving = ledger.saveEntry(inputEntry(ledger))
  await tick()
  auth.enterGuest()
  release()
  assert.equal(await saving, false)
  assert.equal(ledger.accountId, null)
  assert.deepEqual(storage.get('moji-demo-v1'), guest)
  handler = server
})

await check('保留旧账号本机记录用于导出，不把它们自动上传到云端', async () => {
  const { auth, ledger } = await fresh()
  const key = `moji-user-v1:${users[0].id}:ledger`
  const old = { books: clone(ledger.books), entries: clone(ledger.entries), activeBookId: ledger.activeBookId }
  storage.set(key, old)
  await auth.login()
  assert.equal(ledger.entries.length, 0)
  assert.equal(ledger.legacyEntries.length, old.entries.length)
  assert.deepEqual(storage.get(key), old)
  const sharedId = await ledger.addBook('共同', true)
  assert.ok(ledger.books.some(book => book.id === sharedId && book.kind === 'shared' && book.currentUserId === users[0].id))
  assert.equal(ledger.openFamilyExample(), '')
})

await check('云端首次读取失败仍可重试，未就绪时阻止写入', async () => {
  const { auth, ledger } = await fresh()
  handler = async options => {
    if (new URL(options.url).pathname.endsWith('/ledger')) throw new Error('offline')
    return server(options)
  }
  await auth.login()
  assert.equal(auth.user.id, users[0].id)
  assert.equal(ledger.cloudReady, false)
  assert.equal(await ledger.saveEntry(inputEntry(ledger)), false)
  handler = server
  assert.equal(await ledger.refreshCloud(), true)
  assert.equal(ledger.cloudReady, true)
})

await check('智能识别只返回草稿，确认前不修改账单，并使用当前账本和登录凭证', async () => {
  const { auth, ledger } = await fresh()
  await auth.login()
  const before = ledger.entries.length
  handler = async options => {
    assert.ok(options.header.Authorization)
    assert.equal(new URL(options.url).pathname, '/api/v1/assistant/parse')
    assert.equal(options.data.bookId, ledger.activeBookId)
    return { statusCode: 200, data: { drafts: [{ ...inputEntry(ledger), key: '11111111-2222-4333-8444-555555555555', status: 'pending', accountDefaulted: true }] } }
  }
  const result = await ledger.recognizeDrafts('今天早餐8元')
  assert.equal(result.drafts[0].amount, 800)
  assert.equal(ledger.entries.length, before)
  assert.equal(cloudStates.get(users[0].id).entries.length, 0)
  handler = server
})

await check('连续记账接口携带当前身份、账本、文字、图片及待确认上下文，确认前不保存', async () => {
  const { auth, ledger } = await fresh()
  await auth.login()
  const before = ledger.entries.length
  const image = 'data:image/png;base64,dGVzdA=='
  handler = async options => {
    assert.ok(options.header.Authorization)
    assert.equal(new URL(options.url).pathname, '/api/v1/assistant/record')
    assert.equal(options.data.bookId, ledger.activeBookId)
    assert.equal(options.data.text, '是昨天的')
    assert.equal(options.data.history[0].text, '识别账单图片')
    assert.equal(options.data.image, image)
    assert.equal(options.data.ownerId, undefined)
    return { statusCode: 200, data: { drafts: [], clarification: '请确认金额', reply: '请确认金额' } }
  }
  const result = await ledger.recordDrafts({ text: '是昨天的', image, history: [{ role: 'user', text: '识别账单图片' }], drafts: [] })
  assert.equal(result.clarification, '请确认金额')
  assert.equal(ledger.entries.length, before)
  handler = server
})

await check('连续记账的响应迟于切换账本时拒绝展示', async () => {
  const { auth, ledger } = await fresh()
  await auth.login()
  let release
  handler = async () => new Promise(resolve => { release = () => resolve({ statusCode: 200, data: { drafts: [], reply: '旧结果' } }) })
  const request = ledger.recordDrafts({ text: '早餐8元', history: [], drafts: [] })
  const rejected = assert.rejects(request, /账本已变化/)
  await tick()
  ledger.selectBook('another-book')
  release()
  await rejected
  handler = server
})

console.log(`登录与云端记账检查完成：${passed} 项通过；仅使用模拟接口和内存数据。`)
