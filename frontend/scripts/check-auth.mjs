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
  if (clear) { storage.clear(); sessions.clear(); loginCount = 0; sequence = 0; codeHistory.length = 0 }
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
  ledger.saveEntry({ bookId: 'daily', amount: 800, categoryId: 'food', date: '2026-10-24', type: 'expense', note: '账号自己的早餐', account: '微信' })
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

console.log(`登录检查完成：${passed} 项通过；仅使用模拟接口和内存数据。`)
