import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

// 直接检查 TypeScript 源码，无需启动 uni-app，也不读写用户的本地账单。
const toModuleUrl = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
const transpile = source => ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } }).outputText
const formatUrl = toModuleUrl(transpile(await readFile(new URL('../common/utils/format.ts', import.meta.url), 'utf8')))
const compiled = transpile(await readFile(new URL('../common/mock/assistant.ts', import.meta.url), 'utf8'))
const { createExampleDrafts, recordExamples, buildDemoAnalysis } = await import(toModuleUrl(compiled.replace(/from (['"])\.\.\/utils\/format\1/g, `from '${formatUrl}'`)))
const categories = [
  { id: 'food', name: '餐饮', icon: 'food', color: '#fff', type: 'expense' },
  { id: 'house', name: '住房', icon: 'home', color: '#fff', type: 'expense' },
  { id: 'other', name: '其他', icon: 'more', color: '#fff', type: 'expense' },
]
const entry = (id, date, amount, categoryId = 'food', type = 'expense') => ({ id, bookId: 'test', date, amount, categoryId, type, note: id, account: '微信', createdAt: 1 })
const fixture = [entry('餐饮1', '2025-01-01', 20000), entry('餐饮2', '2025-01-02', 20001), entry('房租', '2025-01-03', 180000, 'house'), entry('工资', '2025-01-04', 1200000, 'salary', 'income'), entry('上月餐饮', '2024-12-02', 10000)]
let passed = 0
function check(name, action) { action(); passed++; console.log(`通过：${name}`) }

check('早餐草稿保留8元、08:00与指定账本', () => {
  const [draft] = createExampleDrafts(recordExamples[0].text, 'test')
  assert.equal(draft.amount, 800)
  assert.equal(draft.time, '08:00')
  assert.equal(draft.bookId, 'test')
})
check('多笔草稿独立且不补造时间', () => {
  const drafts = createExampleDrafts(recordExamples[1].text, 'test')
  assert.deepEqual(drafts.map(item => item.amount), [2500, 1800])
  assert.equal(new Set(drafts.map(item => item.key)).size, 2)
  assert.ok(drafts.every(item => !item.time))
})
check('兼职示例为收入且使用明确给出的银行卡', () => {
  const [draft] = createExampleDrafts(recordExamples[2].text, 'test')
  assert.equal(draft.type, 'income')
  assert.equal(draft.amount, 30000)
  assert.equal(draft.account, '银行卡')
})
check('自由输入不伪造识别结果', () => assert.equal(createExampleDrafts('一段不在演示中的输入', 'test'), null))
check('分类汇总排除收入与其他月份', () => {
  const result = buildDemoAnalysis(fixture, categories, '2025-01', 'overview')
  assert.equal(result.total, 220001)
  assert.equal(result.count, 3)
  assert.equal(result.rows[0].label, '住房')
  assert.equal(result.rows[0].percent, 81.8)
})
check('分类明细限定范围且按金额排序', () => {
  const result = buildDemoAnalysis(fixture, categories, '2025-01', 'details', 'food')
  assert.equal(result.total, 40001)
  assert.equal(result.count, 2)
  assert.equal(result.rows[0].amount, 20001)
})
check('月份对比跨年且继承分类', () => {
  const result = buildDemoAnalysis(fixture, categories, '2025-01', 'compare', 'food')
  assert.deepEqual(result.rows.map(item => item.amount), [40001, 10000])
  assert.equal(result.rows[1].label, '2024年12月')
})
check('没有上月记录时不推算增长率', () => {
  const result = buildDemoAnalysis(fixture, categories, '2025-01', 'compare', 'house')
  assert.ok(result.text.includes('暂时无法比较'))
})
check('大额消费严格超过200元', () => {
  const result = buildDemoAnalysis(fixture, categories, '2025-01', 'large')
  assert.equal(result.count, 2)
  assert.equal(result.total, 200001)
  assert.ok(result.rows.every(item => item.amount > 20000))
})
check('空账本有说明且不产生虚构记录', () => {
  const result = buildDemoAnalysis([], categories, '2025-01', 'overview')
  assert.equal(result.total, 0)
  assert.equal(result.rows.length, 0)
  assert.ok(result.text.includes('没有支出记录'))
})
console.log(`助手演示检查完成：${passed}项通过。`)
