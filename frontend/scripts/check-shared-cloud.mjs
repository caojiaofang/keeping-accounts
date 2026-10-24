import assert from 'node:assert/strict'
import path from 'node:path'
import { createPinia, setActivePinia } from 'pinia'
import { moduleUrl, root } from './test-modules.mjs'

const { useLedgerStore } = await import(await moduleUrl(path.join(root, 'store/ledger.ts')))
const { useAuthStore } = await import(await moduleUrl(path.join(root, 'store/auth.ts')))
const { canManageBook, canEditEntry, entryEditor } = await import(await moduleUrl(path.join(root, 'common/services/shared-books.ts')))
const { rememberInvitation, pendingInvitationPath, forgetInvitation, previewInvitation, invitationPath } = await import(await moduleUrl(path.join(root, 'common/services/invitations.ts')))
const { safeLoginRedirect } = await import(await moduleUrl(path.join(root, 'common/services/auth.ts')))
const clone = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value))
const storage = new Map(), sessions = new Map(), books = new Map(), invitations = new Map()
const users = ['1', '2'].map(n => ({ id: `${n.repeat(8)}-${n.repeat(4)}-4${n.repeat(3)}-8${n.repeat(3)}-${n.repeat(12)}`, openid: 'mock-' + n, created_at: '2026-10-24T00:00:00Z', last_login_at: '2026-10-24T00:00:00Z' }))
let identity = users[0], sequence = 0, handler, calls = [], entries = []
const ok = data => ({ statusCode: 200, data: clone(data) })
const fail = (statusCode, detail) => ({ statusCode, data: { detail } })
const visible = (book, user) => book.kind === 'personal' ? book.ownerId === user.id : book.members.some(m => m.userId === user.id && m.status === 'active')
const describe = (book, user) => ({ ...clone(book), currentUserId: user.id })

async function server(options) {
  const url = new URL(options.url), pathname = url.pathname
  calls.push({ path: pathname, data: clone(options.data), header: clone(options.header) })
  if (pathname.endsWith('/wechat/login')) {
    const access_token = String.fromCharCode(65 + sequence++).repeat(43)
    sessions.set(access_token, clone(identity))
    return ok({ access_token, token_type: 'bearer', expires_in: 604800, is_new_user: false, user: identity })
  }
  if (pathname.endsWith('/preview')) {
    const invitation = [...invitations.values()].find(i => i.token === options.data.token && !i.revoked)
    if (!invitation) return fail(410, '邀请已失效')
    const book = books.get(invitation.bookId)
    return ok({ bookId: book.id, bookName: book.name, inviterName: book.members.find(m => m.role === 'owner').name, expiresAt: invitation.expiresAt })
  }
  const token = options.header.Authorization?.slice(7), user = sessions.get(token)
  if (!user) return fail(401, '登录已过期')
  if (pathname.endsWith('/logout')) { sessions.delete(token); return { statusCode: 204, data: '' } }
  if (pathname.endsWith('/auth/me')) return ok(user)
  const personal = 'daily-' + user.id
  if (!books.has(personal)) books.set(personal, { id: personal, ownerId: user.id, name: '日常账本', budget: 500000, version: 1, kind: 'personal', members: [], icon: 'leaf', color: '#e1eee7', description: '' })
  if (pathname.endsWith('/ledger')) {
    const allowed = [...books.values()].filter(b => visible(b, user))
    return ok({ books: allowed.map(b => describe(b, user)), entries: entries.filter(e => allowed.some(b => b.id === e.bookId)) })
  }
  if (pathname === '/api/v1/books') {
    const input = options.data
    if (!books.has(input.id)) books.set(input.id, { ...books.get(personal), id: input.id, name: input.name, kind: input.kind || 'personal', members: input.kind === 'shared' ? [{ userId: user.id, name: input.memberName, role: 'owner', status: 'active', color: '#dcece1' }] : [] })
    return ok(describe(books.get(input.id), user))
  }
  if (pathname.endsWith('/accept')) {
    const invite = [...invitations.values()].find(i => i.token === options.data.token && !i.revoked)
    if (!invite) return fail(410, '邀请已失效')
    const book = books.get(invite.bookId)
    if (!book.members.some(m => m.userId === user.id && m.status === 'active')) { book.members.push({ userId: user.id, name: options.data.name, role: 'member', status: 'active', color: '#dcece1' }); book.version++ }
    return ok(describe(book, user))
  }
  const id = pathname.split('/')[4], book = books.get(id)
  if (!book || !visible(book, user)) return fail(404, '账本不存在')
  if (pathname.endsWith('/invitations')) {
    if (book.ownerId !== user.id) return fail(403, '只有创建者可以邀请')
    const input = options.data
    if (invitations.has(input.id)) return ok(invitations.get(input.id))
    for (const invite of invitations.values()) if (invite.bookId === book.id) invite.revoked = true
    const invite = { id: input.id, token: 's1_' + input.id.replaceAll('-', '') + '_' + 'a'.repeat(64), bookId: book.id, expiresAt: Date.now() + 604800000, revoked: false }
    invitations.set(input.id, invite); book.version++
    return ok(invite)
  }
  if (pathname.includes('/invitations/')) { invitations.get(pathname.split('/').at(-1)).revoked = true; book.version++; return { statusCode: 204, data: '' } }
  if (pathname.endsWith('/leave') || pathname.endsWith('/close')) {
    book.members.find(m => m.userId === user.id).status = 'left'
    return { statusCode: 204, data: '' }
  }
  if (pathname.endsWith('/transfer')) {
    book.members.find(m => m.role === 'owner').role = 'member'
    book.members.find(m => m.userId === options.data.userId).role = 'owner'
    book.ownerId = options.data.userId; book.version++
    return ok(describe(book, user))
  }
  return fail(404, '接口不存在')
}
globalThis.getCurrentPages = () => [{ route: 'pages/shared-books/index' }]
globalThis.uni = {
  getStorageSync: key => clone(storage.get(key)), setStorageSync: (key, value) => storage.set(key, clone(value)), removeStorageSync: key => storage.delete(key), showToast() {}, reLaunch() {},
  login(options) { options.success({ code: 'mock-code' }) },
  request(options) { Promise.resolve().then(() => handler(options)).then(result => options.success(result), () => options.fail({})) },
}
async function fresh() {
  storage.clear(); sessions.clear(); books.clear(); invitations.clear(); entries = []; calls = []; identity = users[0]; sequence = 0; handler = server
  setActivePinia(createPinia())
  const auth = useAuthStore(); await auth.bootstrap(); await auth.login()
  return { auth, ledger: useLedgerStore() }
}
async function login(auth, user) { await auth.logout(); identity = user; await auth.login() }
let passed = 0
async function check(name, fn) { await fn(); passed++; console.log(`通过：${name}`) }

await check('真实共享账本创建后使用当前用户身份，邀请只进入预览不会自动加入', async () => {
  const { auth, ledger } = await fresh()
  const id = await ledger.addBook('我们的小家', true)
  const book = ledger.books.find(b => b.id === id)
  assert.equal(canManageBook(book), true)
  const invite = await ledger.createInvitation(id)
  await auth.logout()
  const preview = await previewInvitation(invite.token)
  assert.deepEqual(Object.keys(preview).sort(), ['bookId','bookName','inviterName','expiresAt'].sort())
  assert.equal(books.get(id).members.length, 1)
  assert.equal(calls.at(-1).header.Authorization, undefined)
  assert.equal(ledger.accountId, null)
})

await check('登录返回邀请页的地址受白名单限制，并能保留和清理邀请目标', async () => {
  await fresh()
  const token = 's1_' + '1'.repeat(32) + '_' + 'a'.repeat(64)
  rememberInvitation(token)
  assert.equal(pendingInvitationPath(), invitationPath(token))
  assert.equal(safeLoginRedirect(encodeURIComponent(invitationPath(token))), invitationPath(token))
  assert.equal(safeLoginRedirect(invitationPath(token) + '&next=https://bad.test'), '/pages/ledger/index')
  forgetInvitation(); assert.equal(pendingInvitationPath(), '')
})

await check('成员确认加入后读取共同账单，自己的权限与创建者权限分别正确', async () => {
  const { auth, ledger } = await fresh()
  const id = await ledger.addBook('家庭', true), invite = await ledger.createInvitation(id)
  entries = [{ id: 'owner-entry', bookId: id, createdBy: users[0].id, updatedBy: users[0].id, type: 'expense', categoryId: 'food', amount: 800, date: '2026-10-24', account: '微信', note: '', createdAt: 1 }, { id: 'mine', bookId: id, createdBy: users[1].id, type: 'expense', categoryId: 'food', amount: 1200, date: '2026-10-24', account: '微信', note: '', createdAt: 2 }]
  await login(auth, users[1])
  assert.equal(await ledger.acceptInvitation(invite.token, '小林'), id)
  const book = ledger.books.find(b => b.id === id)
  assert.equal(ledger.entries.length, 2)
  assert.equal(canManageBook(book), false)
  assert.equal(canEditEntry(book, ledger.entries[0]), false)
  assert.equal(canEditEntry(book, ledger.entries[1]), true)
  assert.equal(await ledger.updateBudget(10000), false)
  assert.equal(await ledger.createInvitation(id), undefined)
  assert.deepEqual(Object.keys(calls.find(c => c.path.endsWith('/accept')).data).sort(), ['name','token'])
})

await check('加入响应丢失后显式重试不会重复成员，刷新后能看到共同历史', async () => {
  const { auth, ledger } = await fresh()
  const id = await ledger.addBook('家庭', true), invite = await ledger.createInvitation(id)
  await login(auth, users[1])
  handler = async options => { const response = await server(options); if (options.url.endsWith('/accept')) throw new Error('response lost'); return response }
  assert.equal(await ledger.acceptInvitation(invite.token, '小林'), '')
  handler = server
  assert.equal(await ledger.acceptInvitation(invite.token, '小林'), id)
  assert.equal(books.get(id).members.length, 2)
})

await check('撤销和重新生成邀请更新本机状态，转让与退出清除旧权限和缓存', async () => {
  const { auth, ledger } = await fresh()
  const id = await ledger.addBook('家庭', true), first = await ledger.createInvitation(id)
  assert.equal(await ledger.revokeInvitation(first.token), true)
  assert.equal(ledger.invitations[0].revoked, true)
  const second = await ledger.createInvitation(id, true)
  assert.notEqual(second.token, first.token)
  await login(auth, users[1]); await ledger.acceptInvitation(second.token, '小林')
  await login(auth, users[0]); ledger.selectBook(id)
  assert.equal(await ledger.transferBook(id, users[1].id), true)
  assert.equal(canManageBook(ledger.activeBook), false)
  assert.equal(await ledger.leaveBook(id), true)
  assert.equal(ledger.books.some(b => b.id === id), false)
  assert.notEqual(ledger.activeBookId, id)
})

await check('成员失效后的刷新清除共同记录，修改人仍按真实身份展示', async () => {
  const { auth, ledger } = await fresh()
  const id = await ledger.addBook('家庭', true), invite = await ledger.createInvitation(id)
  await login(auth, users[1]); await ledger.acceptInvitation(invite.token, '小林')
  const book = ledger.books.find(b => b.id === id)
  assert.equal(entryEditor(book, { createdBy: users[1].id, updatedBy: users[0].id }), '创建者')
  books.get(id).members.find(m => m.userId === users[1].id).status = 'removed'
  await ledger.refreshCloud()
  assert.equal(ledger.books.some(b => b.id === id), false)
  assert.equal(ledger.entries.some(e => e.bookId === id), false)
})

console.log(`云端多人账本检查完成：${passed} 项通过；仅使用模拟服务与内存。`)
