import type { Category, Entry } from './types'
import { localDate, money, monthLabel, shiftMonth } from './format'

export type DraftStatus = 'pending' | 'saved' | 'dismissed'
export interface AssistantDraft extends Omit<Entry, 'id' | 'createdAt'> {
  key: string
  status: DraftStatus
}
export const recordExamples = [
  { label: '记早餐', text: '今天早上八点吃早餐8块钱' },
  { label: '一次记多笔', text: '今天午饭25元，下午咖啡18元' },
  { label: '记一笔收入', text: '昨天兼职收入300元，存入银行卡' },
]

// 仅提供预置交互示例；后续由 Python 后端的大模型识别接口替换。
export function createExampleDrafts(text: string, bookId: string): AssistantDraft[] | null {
  const index = recordExamples.findIndex(example => example.text === text.trim())
  if (index < 0) return null
  const yesterday = new Date()
  yesterday.setDate(yesterday.getDate() - 1)
  const base = { bookId, type: 'expense' as const, date: localDate(), account: '微信', status: 'pending' as const }
  const batch = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
  if (index === 0) return [{ ...base, key: `${batch}-0`, categoryId: 'food', amount: 800, time: '08:00', note: '早餐' }]
  if (index === 1) return [
    { ...base, key: `${batch}-0`, categoryId: 'food', amount: 2500, note: '午饭' },
    { ...base, key: `${batch}-1`, categoryId: 'coffee', amount: 1800, note: '下午咖啡' },
  ]
  return [{ ...base, key: `${batch}-0`, type: 'income', categoryId: 'parttime', amount: 30000, date: localDate(yesterday), account: '银行卡', note: '兼职收入' }]
}

export type AnalysisTopic = 'overview' | 'details' | 'compare' | 'large' | 'advice'
export const analysisQuestions: { topic: AnalysisTopic; text: string }[] = [
  { topic: 'overview', text: '这个月的花费主要集中在哪？' },
  { topic: 'details', text: '餐饮具体都花在哪了？' },
  { topic: 'compare', text: '和上个月相比呢？' },
  { topic: 'large', text: '列出超过200元的消费' },
  { topic: 'advice', text: '有哪些支出可以减少？' },
]

export interface AnalysisResult {
  title: string
  text: string
  total: number
  count: number
  scope: string
  rows: { label: string; amount: number; percent?: number; icon: string; color: string; detail?: string }[]
  footnote?: string
}

// 演示答案由当前账本真实的本地记录生成，不伪装成模型返回结果。
export function buildDemoAnalysis(allEntries: Entry[], categories: Category[], month: string, topic: AnalysisTopic, categoryId?: string): AnalysisResult {
  const today = localDate()
  const inMonth = (target: string) => allEntries.filter(entry => entry.type === 'expense' && entry.date.startsWith(target) && (target !== today.slice(0, 7) || entry.date <= today))
  const entries = inMonth(month)
  const selected = categoryId ? entries.filter(entry => entry.categoryId === categoryId) : entries
  const sum = (items: Entry[]) => items.reduce((total, entry) => total + entry.amount, 0)
  const categoryOf = (id: string) => categories.find(category => category.id === id) || categories.find(category => category.id === 'other')!
  const scope = `${monthLabel(month)}${month === today.slice(0, 7) ? `，截至${Number(today.slice(5, 7))}月${Number(today.slice(8))}日` : ''}`
  const total = sum(selected)
  const result: AnalysisResult = { title: '本月支出概览', text: '', total, count: selected.length, scope, rows: [] }
  if (topic === 'compare') {
    const previous = inMonth(shiftMonth(month, -1)).filter(entry => !categoryId || entry.categoryId === categoryId)
    const previousTotal = sum(previous)
    const label = categoryId ? categoryOf(categoryId).name : '全部支出'
    result.title = `${label} · 月份对比`
    result.rows = [
      { label: monthLabel(month), amount: total, icon: 'calendar', color: '#e1eee7' },
      { label: monthLabel(shiftMonth(month, -1)), amount: previousTotal, icon: 'calendar', color: '#edf0ea' },
    ]
    result.text = previous.length ? `${label}本期¥${money(total)}，上月¥${money(previousTotal)}，${total >= previousTotal ? '增加' : '减少'}¥${money(total - previousTotal)}。` : `本期${label}为¥${money(total)}。上个月没有对应的支出记录，暂时无法比较变化。`
    result.footnote = month === today.slice(0, 7) ? '本月截至今天，与上月全月比较；记录可能不完整，差额不代表消费趋势。' : '按两个完整自然月的已记账数据比较；没有记录不等于没有消费。'
    return result
  }
  if (!selected.length) {
    result.title = categoryId ? `${categoryOf(categoryId).name}支出` : '还没有支出记录'
    result.text = categoryId ? '这个月还没有记下这一类支出，可以先看看其他分类。' : '当前月份没有支出记录。记下一笔后，就能在这里查看花费分布。'
    return result
  }
  const detailRow = (entry: Entry) => ({ label: entry.note || categoryOf(entry.categoryId).name, amount: entry.amount, icon: categoryOf(entry.categoryId).icon, color: categoryOf(entry.categoryId).color, detail: `${entry.date}${entry.time ? ` ${entry.time}` : ''} · ${entry.account}` })
  if (topic === 'details') {
    result.title = `${categoryOf(categoryId || 'food').name}消费明细`
    result.rows = [...selected].sort((a, b) => b.amount - a.amount).slice(0, 8).map(detailRow)
    result.text = `共记录${selected.length}笔，合计¥${money(total)}。下面按金额从高到低列出${result.rows.length}笔。`
    result.footnote = '金额来自本地账单，明细最多展示8笔。'
    return result
  }
  if (topic === 'large') {
    const large = [...entries].filter(entry => entry.amount > 20000).sort((a, b) => b.amount - a.amount)
    result.title = '超过200元的消费'
    result.total = sum(large)
    result.count = large.length
    result.rows = large.slice(0, 8).map(detailRow)
    result.text = large.length ? `找到${large.length}笔超过200元的支出，合计¥${money(result.total)}。` : '这个月没有超过200元的支出记录。'
    result.footnote = '按单笔金额严格大于200元筛选，最多展示8笔。'
    return result
  }
  const totals = new Map<string, number>()
  entries.forEach(entry => totals.set(entry.categoryId, (totals.get(entry.categoryId) || 0) + entry.amount))
  const ranking = [...totals].sort((a, b) => b[1] - a[1])
  result.rows = ranking.slice(0, 4).map(([id, amount]) => ({ label: categoryOf(id).name, amount, percent: Math.round(amount / sum(entries) * 1000) / 10, icon: categoryOf(id).icon, color: categoryOf(id).color }))
  const top = result.rows[0]
  result.text = `这个月支出主要集中在${top.label}，共¥${money(top.amount)}，占总支出的${top.percent}%。`
  result.footnote = '按支出分类统计，不含收入；仅反映当前账本已经记录的数据。'
  if (topic === 'advice') {
    const flexible = entries.filter(entry => ['coffee', 'shopping', 'play'].includes(entry.categoryId))
    result.title = '从可调整的支出看起'
    result.text = flexible.length ? `咖啡茶饮、购物和娱乐共记录¥${money(sum(flexible))}。可以先回看这些消费是否符合自己的计划，再决定下个月的分类预算。` : '目前记录不足以判断哪些消费可以减少。可以先区分固定开销和可调整开销，再结合自己的生活需要设置预算。'
    result.footnote = '这是基于本地分类的演示提示；仅凭金额无法判断消费是否必要。'
  }
  return result
}
