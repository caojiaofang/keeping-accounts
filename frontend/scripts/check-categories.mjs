import assert from 'node:assert/strict'
import path from 'node:path'
import { createPinia, setActivePinia } from 'pinia'
import { moduleUrl, root } from './test-modules.mjs'
const { useLedgerStore } = await import(await moduleUrl(path.join(root, 'store/ledger.ts')))
const storage = new Map(), toasts = []
globalThis.uni = { getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, JSON.parse(JSON.stringify(value))), removeStorageSync: key => storage.delete(key), showToast: value => toasts.push(value.title) }
setActivePinia(createPinia())
const ledger = useLedgerStore(); ledger.hydrate()
const book = ledger.activeBookId
assert.equal(await ledger.saveCategory(book, 'expense', '宠物'), true)
const category = { ...ledger.customCategories[0] }
assert.equal(ledger.categoriesFor(book, 'expense').find(item => item.id === category.id).name, '宠物')
assert.equal(await ledger.saveCategory(book, 'expense', '宠物'), false)
assert.equal(await ledger.saveCategory(book, 'expense', '餐饮'), false)
assert.equal(await ledger.saveCategory(book, 'expense', '   '), false)
assert.equal(await ledger.saveCategory(book, 'income', '宠物'), true)
const other = await ledger.addBook('独立账本')
assert.equal(ledger.categoriesFor(other, 'expense').some(item => item.id === category.id), false)
const bill = { bookId: book, type: 'expense', categoryId: category.id, amount: 1000, date: '2026-11-16', account: '微信', note: '' }
assert.equal(await ledger.saveEntry({ ...bill, bookId: other }), false)
assert.equal(await ledger.saveEntry({ ...bill, type: 'income' }), false)
assert.equal(await ledger.saveEntry(bill, undefined, 'category-bill'), true)
assert.equal(await ledger.saveCategory(book, 'expense', '猫咪用品', category), true)
assert.equal(await ledger.saveCategory(book, 'expense', '旧名称', category), false)
assert.equal(ledger.categoryOf(category.id).name, '猫咪用品')
const latest = { ...ledger.customCategories.find(item => item.id === category.id) }
assert.equal(await ledger.deleteCategory(latest), true)
assert.equal(ledger.categoriesFor(book, 'expense').some(item => item.id === category.id), false)
assert.equal(ledger.categoriesFor(book, 'expense', category.id).some(item => item.id === category.id), true)
assert.equal(await ledger.saveEntry(bill), false)
assert.equal(await ledger.saveEntry({ ...bill, amount: 1200, version: 1 }, 'category-bill'), true)
assert.equal(ledger.entries.find(item => item.id === 'category-bill').amount, 1200)
assert.equal(ledger.categoryOf(category.id).name, '猫咪用品')
assert.equal(await ledger.saveCategory(book, 'expense', '猫咪用品'), true)
assert.notEqual(ledger.customCategories.find(item => !item.isDeleted && item.name === '猫咪用品').id, category.id)
setActivePinia(createPinia()); const restored = useLedgerStore(); restored.hydrate()
assert.equal(restored.categoryOf(category.id).name, '猫咪用品')
restored.switchAccount('user-a', 'token-a')
assert.equal(restored.customCategories.length, 0)
let finish
uni.request = options => { finish = options.success }
const read = restored.refreshCloud()
restored.switchAccount('user-b', 'token-b')
finish({ statusCode: 200, data: { books: [{ id: 'foreign', name: '其他账号', budget: 0 }], entries: [], customCategories: [category] } })
assert.equal(await read, false)
assert.equal(restored.customCategories.length, 0)
restored.switchAccount(null)
assert.equal(restored.categoryOf(category.id).name, '猫咪用品')
restored.resetDemo(); assert.equal(restored.customCategories.length, 0)
console.log('分类检查通过：账本隔离、收支校验、公共名称保护、版本冲突、删除保留、持久化及账号切换')
