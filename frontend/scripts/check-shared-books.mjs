import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createPinia, setActivePinia } from 'pinia'
import ts from 'typescript'

const url = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
const compile = async path => ts.transpileModule(await readFile(new URL(path, import.meta.url), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } }).outputText
const formatUrl = url(await compile('../common/utils/format.ts'))
const sharedUrl = url((await compile('../common/services/shared-books.ts')).replace("'../utils/format'", `'${formatUrl}'`))
const categoriesUrl = url(await compile('../common/data/categories.ts'))
const mockUrl = url((await compile('../common/mock/ledger.ts')).replace("'@/common/utils/format'", `'${formatUrl}'`))
const storeSource = (await compile('../store/ledger.ts'))
  .replace("'vue'", `'${import.meta.resolve('vue')}'`)
  .replace("'pinia'", `'${import.meta.resolve('pinia')}'`)
  .replace("'@/common/utils/format'", `'${formatUrl}'`)
  .replace("'@/common/data/categories'", `'${categoriesUrl}'`)
  .replace("'@/common/mock/ledger'", `'${mockUrl}'`)
  .replace("'@/common/services/shared-books'", `'${sharedUrl}'`)
const { useLedgerStore } = await import(url(storeSource))
const { activeMembers, canEditEntry, canManageBook, canWriteBook, DEMO_USER_ID, entryAuthor, familyExample, memberTotals, validInvitation } = await import(sharedUrl)
const storage = new Map()
globalThis.uni = { getStorageSync: key => storage.has(key) ? JSON.parse(JSON.stringify(storage.get(key))) : undefined, setStorageSync: (key, value) => storage.set(key, JSON.parse(JSON.stringify(value))), showToast: () => {} }
let passed = 0
function check(name, action) { action(); passed++; console.log(`通过：${name}`) }
const fresh = () => { setActivePinia(createPinia()); const store = useLedgerStore(); store.hydrate(); return store }

check('旧本地数据兼容，不自动把个人小家账本转为共享或填入新示例', () => {
  const original = familyExample(false).entries[0]
  const books = [{ id: 'legacy', name: '旧小家', budget: 50000, icon: 'home', color: '#fff', description: '个人记录' }]
  storage.set('moji-demo-v1', { entries: [{ ...original, bookId: 'legacy' }], books, activeBookId: 'legacy' })
  const store = fresh()
  assert.equal(store.books.length, 1)
  assert.equal(store.entries.length, 1)
  assert.equal(store.books[0].kind, undefined)
  assert.deepEqual([...store.invitations], [])
  storage.clear()
})
check('主动创建示例才写入，重复打开不重复添加成员或账单', () => {
  const store = fresh()
  const count = store.entries.length
  const books = store.books.length
  const id = store.openFamilyExample()
  store.openFamilyExample()
  assert.equal(store.entries.length, count + 3)
  assert.equal(store.books.length, books + 1)
  assert.equal(activeMembers(store.books.find(book => book.id === id)).length, 2)
  assert.equal(store.books.find(book => book.id === 'home').kind, undefined)
})
check('成员可看共用账本，只能维护本人账单；创建者可以维护所有共同账单', () => {
  const { book, entries } = familyExample(true)
  const mine = entries.find(entry => entry.createdBy === DEMO_USER_ID)
  const other = entries.find(entry => entry.createdBy !== DEMO_USER_ID)
  assert.ok(canWriteBook(book))
  assert.equal(canManageBook(book), false)
  assert.ok(canEditEntry(book, mine))
  assert.equal(canEditEntry(book, other), false)
  assert.ok(canEditEntry(book, mine, 'demo-partner'))
  assert.equal(canWriteBook(book, 'stranger'), false)
  book.members.find(member => member.userId === DEMO_USER_ID).status = 'removed'
  assert.equal(canEditEntry(book, mine), false)
})
check('在 store 中拒绝越权修改、删除、预算设置，保留原始账单', () => {
  const store = fresh()
  const id = store.openFamilyExample(true)
  const other = store.entries.find(entry => entry.bookId === id && entry.createdBy === 'demo-partner')
  const original = JSON.stringify(other)
  assert.equal(store.saveEntry({ ...other, amount: 1 }, other.id), false)
  assert.equal(store.deleteEntry(other.id), false)
  assert.equal(JSON.stringify(other), original)
  const before = store.activeBook.budget
  assert.equal(store.updateBudget(1), false)
  assert.equal(store.activeBook.budget, before)
  assert.equal(store.removeMember(id, 'demo-partner'), false)
  assert.equal(store.createInvitation(id), undefined)
})
check('新账单记录本人，编辑不改记账人，不允许把共同账单转移到个人账本', () => {
  const store = fresh()
  const id = store.openFamilyExample()
  const other = store.entries.find(entry => entry.bookId === id && entry.createdBy === 'demo-partner')
  assert.ok(store.saveEntry({ ...other, createdBy: DEMO_USER_ID, amount: 100 }, other.id))
  assert.equal(other.createdBy, 'demo-partner')
  assert.equal(store.entries.find(entry => entry.id === other.id).createdBy, 'demo-partner')
  assert.equal(store.saveEntry({ ...other, bookId: 'daily' }, other.id), false)
  assert.ok(store.saveEntry({ ...other, createdBy: 'someone-else', note: '本人新增' }))
  assert.equal(store.entries.at(-1).createdBy, DEMO_USER_ID)
})
check('示例邀请加入幂等，保留邀请者为创建者，本人为成员', () => {
  const store = fresh()
  const count = store.entries.length
  const id = store.openFamilyExample(true)
  store.openFamilyExample(true)
  assert.equal(store.entries.length, count)
  const book = store.books.find(book => book.id === id)
  assert.equal(activeMembers(book).find(member => member.userId === DEMO_USER_ID).role, 'member')
})
check('邀请有七天有效期，重新生成或撤销后旧邀请立即失效', () => {
  const store = fresh()
  const id = store.openFamilyExample()
  const book = store.books.find(book => book.id === id)
  const first = store.createInvitation(id)
  assert.ok(validInvitation(first, book))
  assert.equal(store.createInvitation(id).token, first.token)
  assert.equal(validInvitation(first, book, first.expiresAt), false)
  assert.equal(validInvitation(first, { ...book, id: 'other' }), false)
  const second = store.createInvitation(id, true)
  assert.notEqual(second.token, first.token)
  assert.equal(validInvitation(store.invitations.find(item => item.token === first.token), book), false)
  store.revokeInvitation(second.token)
  assert.equal(validInvitation(second, book), false)
})
check('移出成员保留历史账单、统计及记账人，并使旧邀请失效，创建者不能移出自己', () => {
  const store = fresh()
  const id = store.openFamilyExample()
  const book = store.books.find(book => book.id === id)
  const month = store.entries.find(entry => entry.bookId === id).date.slice(0, 7)
  const before = memberTotals(store.entries, id, month)
  const invite = store.createInvitation(id)
  assert.ok(store.removeMember(id, 'demo-partner'))
  assert.deepEqual(memberTotals(store.entries, id, month), before)
  assert.equal(activeMembers(book).length, 1)
  assert.ok(entryAuthor(book, store.entries.find(entry => entry.bookId === id && entry.createdBy === 'demo-partner')).includes('已移出'))
  assert.equal(validInvitation(invite, book), false)
  assert.equal(store.removeMember(id, DEMO_USER_ID), false)
})
check('共享数据、成员移出和撤销邀请持久化，重新读取后不复活', () => {
  const store = fresh()
  const book = store.books.find(book => book.id === 'shared-family-example')
  assert.equal(activeMembers(book).length, 1)
  assert.ok(store.invitations.every(invitation => invitation.revoked))
  assert.ok(store.entries.some(entry => entry.createdBy === DEMO_USER_ID))
})
console.log(`多人账本检查完成：${passed} 项通过；检查仅使用内存存储，未访问用户数据。`)
