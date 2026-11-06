/** 表单与 API 的边界：金额以字符串编辑，只在校验后转换成整数分。 */
import type * as B from '../types/budget'
import { newId } from '../utils/id'

export interface BudgetItemForm {
  id: string; groupId: string; name: string; kind: B.BudgetItemKind; amount: string
  dueDay: string; expected: string; anchor: string; cycle: 1 | 6 | 12; note: string
}
export interface BudgetForm { income: string; groups: B.BudgetGroup[]; items: BudgetItemForm[] }
export function cents(value: string, positive = false): number {
  if (!/^\d{1,7}(\.\d{1,2})?$/.test(value.trim())) throw new Error('金额请输入数字，最多保留两位小数')
  const [whole, decimal = ''] = value.trim().split('.')
  const amount = Number(whole) * 100 + Number(decimal.padEnd(2, '0'))
  if (amount > 999999999 || (positive && amount === 0)) throw new Error(positive ? '金额须大于零且不超过 9,999,999.99 元' : '金额不能超过 9,999,999.99 元')
  return amount
}
export function yuan(value: number): string { return (value / 100).toFixed(2) }
/** 固定月额的比较提示，不读取实际存入，也不代替已安排的逐月覆盖。 */
export function periodProjection(monthly: string, months: 1 | 6 | 12, expected = '0') {
  const planned = cents(monthly) * months
  return { planned, difference: planned - cents(expected) }
}
export function toBudgetForm(plan: B.BudgetPlan | null): BudgetForm {
  return { income: plan ? yuan(plan.plannedIncome) : '', groups: plan ? plan.groups.map(g => ({ ...g })) : [], items: plan ? plan.items.map(i => ({ id: i.id, groupId: i.groupId, name: i.name, kind: i.kind, amount: yuan(i.monthlyAmount), dueDay: i.dueDay ? String(i.dueDay) : '', expected: i.reserve ? yuan(i.reserve.expectedAmount) : '', anchor: i.reserve?.anchorDate || '', cycle: i.reserve?.cycleMonths || 12, note: i.note })) : [] }
}
function date(value: string): boolean {
  if (!/^(20\d{2}|2100)-\d{2}-\d{2}$/.test(value)) return false
  const parsed = new Date(value + 'T00:00:00Z')
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value
}
export function fromBudgetForm(form: BudgetForm): B.BudgetPlan {
  if (form.groups.length > 20 || form.items.length > 200) throw new Error('最多 20 个分组、200 个项目')
  const groups = form.groups.map(g => ({ id: g.id, name: g.name.trim() }))
  if (groups.some(g => !g.name || g.name.length > 24)) throw new Error('分组名称须为 1—24 个字')
  const ids = new Set(groups.map(g => g.id))
  const items = form.items.map(i => {
    const name = i.name.trim()
    if (!name || name.length > 24 || !ids.has(i.groupId)) throw new Error('请填写项目名称并选择有效分组')
    if (i.note.length > 80) throw new Error('备注最多 80 个字')
    const dueDay = i.kind === 'expense' && i.dueDay ? Number(i.dueDay) : null
    if (dueDay !== null && (!/^\d{1,2}$/.test(i.dueDay) || dueDay < 1 || dueDay > 31)) throw new Error('缴费日须为 1—31，或留空')
    if (i.kind === 'reserve' && (!date(i.anchor) || ![1, 6, 12].includes(i.cycle))) throw new Error('请填写有效的预计缴费日期与周期')
    return { id: i.id, groupId: i.groupId, name, kind: i.kind, monthlyAmount: cents(i.amount), dueDay, reserve: i.kind === 'reserve' ? { expectedAmount: cents(i.expected, true), anchorDate: i.anchor, cycleMonths: i.cycle } : null, note: i.note.trim() }
  })
  if (new Set(items.map(i => i.id)).size !== items.length || ids.size !== groups.length) throw new Error('分组或项目重复，请刷新后重试')
  return { plannedIncome: cents(form.income), groups, items }
}
export function newBudgetItem(groupId: string, anchor: string): BudgetItemForm {
  return { id: newId(), groupId, name: '', kind: 'expense', amount: '', dueDay: '', expected: '', anchor, cycle: 12, note: '' }
}
export function singleMonthOverride(plan: B.BudgetPlan, base: B.BudgetPlan): B.BudgetMonthOverride {
  if (plan.items.length !== base.items.length || plan.items.some(i => !base.items.some(b => b.id === i.id && b.kind === i.kind))) throw new Error('单月调整只能修改金额，增删用途请调整后续方案')
  return { plannedIncome: plan.plannedIncome, itemAmounts: plan.items.map(i => ({ itemId: i.id, amount: i.monthlyAmount })) }
}
export function sourcePlan(revisions: B.BudgetRevision[], month: string): B.BudgetPlan | null {
  return [...revisions].sort((a, b) => b.effectiveMonth.localeCompare(a.effectiveMonth)).find(r => r.effectiveMonth <= month)?.plan || null
}
