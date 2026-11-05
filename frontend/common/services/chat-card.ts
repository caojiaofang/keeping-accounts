import type { Book } from '../types'
import type { EntryFilterV2, FactRefV2 } from '../types/chat-v2'
import { categories } from '../data/categories'
import { formatFactV2 } from '../api/chat-v2-codec'

export function cardFacts(ids: string[], facts: FactRefV2[]): FactRefV2[] {
  const lookup = new Map(facts.map(fact => [fact.id, fact]))
  return ids.flatMap(id => lookup.has(id) ? [lookup.get(id)!] : [])
}

export function filterDescriptions(filters: EntryFilterV2, book?: Book): string[] {
  const labels: string[] = [filters.entryType === 'both' ? '收入和支出分别统计' : filters.entryType === 'income' ? '仅收入' : '仅支出']
  if (filters.categoryIds.length) labels.push(`分类：${filters.categoryIds.map(id => categories.find(category => category.id === id)?.name || `历史分类（${id}）`).join('、')}`)
  if (filters.keywords.include.length) labels.push(`备注${filters.keywords.mode === 'all' ? '同时包含' : '包含任意一个'}：${filters.keywords.include.join('、')}`)
  if (filters.keywords.exclude.length) labels.push(`备注不含：${filters.keywords.exclude.join('、')}`)
  if (filters.accounts.length) labels.push(`账户：${filters.accounts.join('、')}`)
  if (filters.creatorIds.length) labels.push(`记账人：${filters.creatorIds.map(id => book?.members?.find(member => member.userId === id)?.name || '历史记账人').join('、')}`)
  const amount = (value: number) => formatFactV2({ id: '', value, unit: 'cents', label: '', sourceCallId: '' })
  if (filters.amountMin !== null) labels.push(`金额${filters.minInclusive ? '大于等于' : '大于'} ${amount(filters.amountMin)}`)
  if (filters.amountMax !== null) labels.push(`金额${filters.maxInclusive ? '小于等于' : '小于'} ${amount(filters.amountMax)}`)
  if (filters.weekdays.length) labels.push(`星期：${filters.weekdays.map(day => ['一', '二', '三', '四', '五', '六', '日'][day - 1]).join('、')}`)
  if (filters.excludedEntryIds.length) labels.push(`已排除指定的 ${filters.excludedEntryIds.length} 笔账单`)
  return labels
}

export function statisticTime(timestamp: number): string {
  const date = new Date(timestamp)
  if (!Number.isFinite(date.getTime())) return '更新时间未知'
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

// 只用服务端事实绘制相对长度；数字和合计始终显示原始事实。
export function factBar(value: number, maximum: number): string {
  return maximum > 0 ? `${Math.min(100, Math.abs(value) / maximum * 100)}%` : '0%'
}
