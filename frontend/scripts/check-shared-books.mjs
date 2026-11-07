import assert from 'node:assert/strict'
import path from 'node:path'
import { createPinia, setActivePinia } from 'pinia'
import { moduleUrl, root } from './test-modules.mjs'

const { useLedgerStore } = await import(await moduleUrl(path.join(root, 'store/ledger.ts')))
const { activeMembers, canEditEntry, canManageBook, canWriteBook, DEMO_USER_ID, entryAuthor, familyExample, memberTotals, validInvitation } = await import(await moduleUrl(path.join(root, 'common/services/shared-books.ts')))
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
check('有效成员和创建者都可维护全部共同账单，非成员和失效成员无权编辑', () => {
  const { book, entries } = familyExample(true)
  const mine = entries.find(entry => entry.createdBy === DEMO_USER_ID)
  const other = entries.find(entry => entry.createdBy !== DEMO_USER_ID)
  assert.ok(canWriteBook(book))
  assert.equal(canManageBook(book), false)
  assert.ok(canEditEntry(book, mine))
  assert.ok(canEditEntry(book, other))
  assert.ok(canEditEntry(book, mine, 'demo-partner'))
  assert.equal(canEditEntry(book, { ...other, bookId: 'another-book' }), false)
  assert.equal(canWriteBook(book, 'stranger'), false)
  assert.equal(canEditEntry(book, other, 'stranger'), false)
  for (const status of ['removed', 'left']) {
    book.members.find(member => member.userId === DEMO_USER_ID).status = status
    assert.equal(canEditEntry(book, mine), false)
    assert.equal(canEditEntry(book, other), false)
  }
})
check('普通成员可修改、删除他人账单，保留记账人且仍不能管理账本', () => {
  const store = fresh()
  const id = store.openFamilyExample(true)
  const other = store.entries.find(entry => entry.bookId === id && entry.createdBy === 'demo-partner')
  assert.equal(store.saveEntry({ ...other, amount: 1 }, other.id), true)
  assert.equal(store.entries.find(entry => entry.id === other.id).amount, 1)
  assert.equal(store.entries.find(entry => entry.id === other.id).createdBy, 'demo-partner')
  assert.equal(store.deleteEntry(other.id), true)
  assert.equal(store.entries.some(entry => entry.id === other.id), false)
  const before = store.activeBook.budget
  assert.equal(store.updateBudget(1), false)
  assert.equal(store.activeBook.budget, before)
  assert.equal(store.removeMember(id, 'demo-partner'), false)
  assert.equal(store.createInvitation(id), undefined)
})
check('退出或移出的成员不能在 store 修改、删除共同账单', () => {
  const store = fresh()
  const id = store.openFamilyExample(true)
  const book = store.books.find(book => book.id === id)
  const other = store.entries.find(entry => entry.bookId === id && entry.createdBy === 'demo-partner')
  for (const status of ['removed', 'left']) {
    book.members.find(member => member.userId === DEMO_USER_ID).status = status
    assert.equal(store.saveEntry({ ...other, amount: 1 }, other.id), false)
    assert.equal(store.deleteEntry(other.id), false)
    assert.equal(store.entries.find(entry => entry.id === other.id).amount, other.amount)
  }
  book.members.find(member => member.userId === DEMO_USER_ID).status = 'active'
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
