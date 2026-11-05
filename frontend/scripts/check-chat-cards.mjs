import assert from 'node:assert/strict'
import path from 'node:path'
import { readFile } from 'node:fs/promises'
import { createSSRApp } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { parse, compileScript } from '@vue/compiler-sfc'
import ts from 'typescript'
import { moduleUrl, root } from './test-modules.mjs'

// 使用真实 Vue 组件渲染，检查用户看到的事实、范围和入口。
const cache = new Map()
async function componentUrl(filename) {
  if (cache.has(filename)) return cache.get(filename)
  const { descriptor } = parse(await readFile(filename, 'utf8'), { filename })
  let source = ts.transpileModule(compileScript(descriptor, { id: filename, inlineTemplate: true }).content, {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
  }).outputText
  for (const match of [...source.matchAll(/from\s+(['"])([^'"]+)\1/g)]) {
    const specifier = match[2]
    const local = specifier.startsWith('@/') ? path.join(root, specifier.slice(2)) : specifier.startsWith('.') ? path.resolve(path.dirname(filename), specifier) : null
    const url = local ? await (local.endsWith('.vue') ? componentUrl(local) : moduleUrl(local.endsWith('.ts') ? local : `${local}.ts`)) : import.meta.resolve(specifier)
    source = source.replace(match[0], `from '${url}'`)
  }
  const url = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
  cache.set(filename, url)
  return url
}
const { default: ResultCard } = await import(await componentUrl(path.join(root, 'components/assistant/cards/ResultCard.vue')))
const { followsLatest } = await import(await moduleUrl(path.join(root, 'common/services/chat-scroll.ts')))
const sample = JSON.parse(await readFile(path.join(root, 'scripts/fixtures/chat-v2-turn.json'), 'utf8'))
const base = () => structuredClone(sample.cards[0])
const book = { id: 'book', name: '家庭账本', kind: 'shared', members: [{ userId: 'member', name: '小林', status: 'active' }] }
const fact = (id, value, unit, label, metric) => ({ id, value, unit, label, metric, sourceCallId: 'q1' })
const render = (card, facts = sample.factRefs, extra = {}) => renderToString(createSSRApp(ResultCard, { card, facts, book, interactive: true, ...extra }))
let passed = 0
async function check(name, run) { await run(); passed++; console.log(`通过：${name}`) }

await check('汇总展示服务端合计、实际日期、更新时间和筛选，零记录仍有统计事实', async () => {
  const card = base()
  card.filters = { ...card.filters, categoryIds: ['food'], keywords: { include: ['咖啡', '<早餐>'], mode: 'all', exclude: ['退款'] }, accounts: ['微信'], creatorIds: ['member'], amountMin: 100, amountMax: 5000, minInclusive: false, maxInclusive: true, weekdays: [1, 7], excludedEntryIds: ['excluded'] }
  const html = await render(card)
  for (const text of ['¥20.50', '2 笔', '2026-10-21', '2026-10-27', '统计于', '历史快照', '同时包含', '&lt;早餐&gt;', '备注不含：退款', '账户：微信', '记账人：小林', '金额大于 ¥1.00', '金额小于等于 ¥50.00', '星期：一、日', '已排除指定的 1 笔账单']) assert.ok(html.includes(text), text)
  const empty = await render(card, sample.factRefs.map(item => ({ ...item, value: 0 })))
  assert.ok(empty.includes('¥0.00') && empty.includes('0 笔'))
})

await check('二十笔长备注明细保留全部匹配总数，不对当前列表重新求和，预览禁用入口', async () => {
  const card = { ...base(), type: 'entries', rows: [] }
  const facts = [fact('q1.total', 123456, 'cents', '支出合计', 'total'), fact('q1.count', 39, 'count', '支出笔数', 'count')]
  for (let index = 0; index < 20; index++) {
    const id = `q1.e${index}`
    facts.push(fact(id, 100, 'cents', '账单金额', 'amount'))
    card.rows.push({ entryId: `entry-${index}`, label: `${index}：很长的早餐备注`.repeat(8).slice(0, 80), date: '2026-10-27', time: '08:30', entryType: 'expense', categoryId: 'food', account: '微信', creatorId: 'member', matchedKeywords: ['早餐'], factIds: [id] })
  }
  const html = await render(card, facts)
  for (const text of ['¥1,234.56', '39 笔', '本卡展示 20 笔', '19：很长的早餐备注', '备注匹配：早餐', '08:30', '小林']) assert.ok(html.includes(text), text)
  assert.equal((html.match(/查看当前账单/g) || []).length, 20)
  const preview = await render(card, facts, { interactive: false, partial: true })
  assert.ok(preview.includes('disabled') && preview.includes('回答保存后可查看账单') && preview.includes('部分结果'))
  assert.ok(!(await render({ ...card, rows: [] }, facts)).includes('查看当前账单'))
})

await check('趋势按真实日期展示，不填零；差额保留负贡献及超过百分百的值', async () => {
  const facts = [fact('q1.total', 1000, 'cents', '本期金额', 'total'), fact('q1.diff', -200, 'cents', '差额', 'difference'), fact('q1.share', -12500, 'basis_points', '差额贡献', 'share'), fact('q1.over', 22500, 'basis_points', '差额贡献', 'share')]
  const trend = { ...base(), type: 'trend', factIds: ['q1.total'], rows: [{ label: '2026-10-21', date: '2026-10-21', factIds: ['q1.total'] }, { label: '2026-10-27', date: '2026-10-27', factIds: ['q1.total'] }] }
  const html = await render(trend, facts)
  assert.ok(html.includes('不把缺失日期补成零消费') && !html.includes('2026-10-22') && html.includes('width:100%'))
  const comparison = await render({ ...base(), type: 'comparison', previousRange: { start: '2026-10-14', end: '2026-10-20' }, factIds: ['q1.diff'], rows: [{ label: '餐饮', factIds: ['q1.share'] }, { label: '其余分组', isOther: true, factIds: ['q1.over'] }] }, facts)
  for (const text of ['2026-10-14', '2026-10-20', '−¥2.00', '−125.00%', '225.00%', '不代表生活原因']) assert.ok(comparison.includes(text), text)
})

await check('上滑阅读停止跟随，内容增长保持阅读位置，下滑近底部恢复', () => {
  assert.equal(followsLatest(500, 450, 1100, 500, true), false)
  assert.equal(followsLatest(450, 450, 1600, 500, false), false)
  assert.equal(followsLatest(450, 1050, 1600, 500, false), true)
  assert.equal(followsLatest(1050, 1050, 1800, 500, true), true)
  assert.equal(followsLatest(0, 20, 1800, 0, false), false)
})
console.log(`聊天卡片与阅读检查完成：${passed} 项通过；使用真实组件渲染与模拟事实。`)
