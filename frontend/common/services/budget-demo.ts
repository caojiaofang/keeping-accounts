/** 本地体验的独立数据源；仅由预算 store 在未登录时使用，不上传云端。 */
import type { BudgetApi } from '../api/budget'
import type * as B from '../types/budget'
import type { Entry } from '../types'
import { ApiError } from '../api/request'
import { newId } from '../utils/id'

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value))
const fail = (message: string, status = 409): never => { throw new ApiError(message, status) }
const today = () => new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10)
const monthNow = () => today().slice(0, 7)
const money = (n: number, positive = false) => { if (!Number.isSafeInteger(n) || n < (positive ? 1 : 0) || n > 999999999) fail('金额须为支持范围内的整数分', 422) }
/** 主动采用模板时生成新 UUID；不自动写入体验或真实账本。 */
export function createFamilyBudgetExample(): B.BudgetPlan {
  const groups = ['固定缴费', '年度预留', '储蓄', '生活费'].map(name => ({ id: newId(), name }))
  const names = ['房贷', '学费', '物业费', '暖气、车险、年度医疗', '每月定存', '提前还贷', '生活费转账', '其他日用购物', '李灿', '李志', '燃、水、电', '油费', '医疗', '人情']
  const amounts = [334000, 110000, 40000, 52000, 200000, 420000, 100000, 50000, 40000, 60000, 34000, 30000, 50000, 40000]
  return { plannedIncome: 1560000, groups, items: names.map((name, i) => ({ id: newId(), name, groupId: groups[i < 3 ? 0 : i === 3 ? 1 : i < 6 ? 2 : 3].id, kind: i === 3 ? 'reserve' : i === 4 || i === 5 ? 'saving' : 'expense', monthlyAmount: amounts[i], dueDay: null, reserve: i === 3 ? { expectedAmount: 610000, anchorDate: today(), cycleMonths: 12 } : null, note: '' })) }
}
const months = (start: string, end: string) => {
  const result: string[] = []
  for (let value = start; value <= end;) {
    result.push(value)
    if (result.length > 120) fail('单次最多安排 120 个月', 422)
    const [year, month] = value.split('-').map(Number)
    value = `${year + (month === 12 ? 1 : 0)}-${String(month % 12 + 1).padStart(2, '0')}`
  }
  return result
}
export function budgetTotals(plan: B.BudgetPlan): B.BudgetTotals {
  const sum = (kind: string) => plan.items.filter(i => i.kind === kind).reduce((n, i) => n + i.monthlyAmount, 0)
  const totalAllocated = plan.items.reduce((n, i) => n + i.monthlyAmount, 0)
  return { plannedIncome: plan.plannedIncome, expenseAmount: sum('expense'), reserveAmount: sum('reserve'), savingAmount: sum('saving'), totalAllocated, unallocated: plan.plannedIncome - totalAllocated, groupTotals: plan.groups.map(g => ({ groupId: g.id, amount: plan.items.filter(i => i.groupId === g.id).reduce((n, i) => n + i.monthlyAmount, 0) })) }
}
interface State {
  plan: B.BudgetPlanResponse
  snapshots: Record<string, B.BudgetMonthResponse>
  bases: Record<string, B.BudgetPlan>
  projects: Record<string, B.BudgetProjectResponse>
  records: B.BudgetFundRecordResponse[]
  goals: B.BudgetGoalResponse[]
  links: Record<string, B.BudgetEntryLinkResponse>
  entryVersions: Record<string, number>
  mutations: Record<string, { digest: string; result: unknown }>
}
export function createBudgetDemo(bookId: string, entries: () => Entry[], persist: (state: unknown) => void, saved?: unknown): BudgetApi {
  const empty: State = { plan: { schemaVersion: 1, bookId, version: 0, revisions: [] }, snapshots: {}, bases: {}, projects: {}, records: [], goals: [], links: {}, entryVersions: {}, mutations: {} }
  let state = saved && typeof saved === 'object' && (saved as State).plan?.bookId === bookId ? clone(saved as State) : empty
  state.bases ||= {}
  state.entryVersions ||= {}
  const previews = new Map<string, { digest: string; expires: number; version: number; dependency: string }>()
  const version = (actual: number, expected: number) => { if (actual !== expected) fail('数据已变化，请刷新后重试') }
  const project = (id: string) => state.projects[id] || fail('预算用途不存在', 404)
  const recordList = (id: string) => state.records.filter(r => r.itemId === id && r.voidedAt === null && r.date <= today())
  // use 始终由当前体验账单派生，修改／删除账单不会留下孤立扣减。
  function sync() {
    for (const link of Object.values(state.links)) {
      if (link.isDeleted) continue
      const entry = entries().find(e => e.id === link.entryId && e.bookId === bookId && e.type === 'expense')
      const p = project(link.itemId)
      const opening = recordList(p.id).find(r => r.kind === 'opening')
      const allowed = entry && entry.date <= today() && entry.date >= p.enabledMonth + '-01' && (!opening || entry.date >= opening.date) && (p.kind !== 'expense' || month(entry.date.slice(0, 7)).plan?.items.some(i => i.id === p.id))
      const goal = state.goals.find(g => g.id === link.goalPeriodId)
      const goalId = entry && goal && entry.date < goal.startDate ? null : link.goalPeriodId
      const old = state.records.find(r => r.entryId === link.entryId && r.kind === 'use' && r.voidedAt === null)
      let linkChanged = false
      if (entry) {
        if (state.entryVersions[entry.id] !== undefined && state.entryVersions[entry.id] !== (entry.version || 1)) linkChanged = true
        state.entryVersions[entry.id] = entry.version || 1
      }
      const desired = allowed && p.kind !== 'expense'
      const changed = old ? !desired || old.amount !== entry!.amount || old.date !== entry!.date || old.note !== entry!.note || old.goalPeriodId !== goalId : desired
      if (!allowed) { link.isDeleted = true; linkChanged = true }
      if (goalId !== link.goalPeriodId) { link.goalPeriodId = goalId; linkChanged = true }
      if (linkChanged) link.version++
      if (!changed) continue
      if (old) { old.voidedAt = Date.now(); old.version++ }
      if (desired) state.records.push({ id: newId(), itemId: p.id, kind: 'use', amount: entry!.amount, date: entry!.date, note: entry!.note, entryId: entry!.id, goalPeriodId: goalId, replacesId: old?.id || null, version: 1, voidedAt: null, createdAt: Date.now(), updatedAt: Date.now(), createdBy: 'demo', updatedBy: 'demo' })
      p.fundVersion++
    }
  }
  function balance(id: string): B.BudgetFundSummary {
    const records = recordList(id), sum = (kind: string) => records.filter(r => r.kind === kind).reduce((n, r) => n + r.amount, 0)
    return { asOfDate: today(), openingAmount: sum('opening'), depositedAmount: sum('deposit'), usedAmount: sum('use'), availableAmount: sum('opening') + sum('deposit') - sum('use') }
  }
  function goalView(g: B.BudgetGoalResponse): B.BudgetGoalResponse {
    const records = recordList(g.itemId)
    const collectedAmount = g.carryInAmount + records.filter(r => r.kind === 'deposit' && r.date >= g.startDate && r.date <= g.targetDate).reduce((n, r) => n + r.amount, 0)
    const usedAmount = records.filter(r => r.kind === 'use' && r.goalPeriodId === g.id).reduce((n, r) => n + r.amount, 0)
    const cutoff = g.closedAt ? new Date(g.closedAt + 8 * 3600000).toISOString().slice(0, 10) : today()
    const pool = records.filter(r => r.date <= cutoff).reduce((n, r) => n + (r.kind === 'use' ? -r.amount : r.amount), 0)
    const availableAmount = Math.min(collectedAmount - usedAmount, pool)
    const fundedAmount = availableAmount + usedAmount, gapAmount = Math.max(g.targetAmount - fundedAmount, 0)
    const window = g.state === 'closed' || today() > g.targetDate ? [] : months((g.startDate > today() ? g.startDate : today()).slice(0, 7), g.targetDate.slice(0, 7))
    return { ...g, collectedAmount, usedAmount, availableAmount, forecast: { fundedAmount, gapAmount, surplusAmount: Math.max(fundedAmount - g.targetAmount, 0), remainingMonths: window.length, overdue: today() > g.targetDate && gapAmount > 0, additionalSchedule: window.map((month, i) => ({ month, amount: Math.floor(gapAmount / window.length) + (i < gapAmount % window.length ? 1 : 0) })) } }
  }
  function month(key: string): B.BudgetMonthResponse {
    if (!/^(20\d{2}|2100)-(0[1-9]|1[0-2])$/.test(key)) fail('月份须为 YYYY-MM', 422)
    if (state.snapshots[key]) return state.snapshots[key]
    const source = [...state.plan.revisions].reverse().find(r => r.effectiveMonth <= key)
    const base = { schemaVersion: 1 as const, bookId, month: key, planVersion: state.plan.version, override: { plannedIncome: null, itemAmounts: [] }, generatedAt: Date.now() }
    return source ? { ...base, state: 'preview', snapshotId: null, version: 0, sourceRevisionId: source.id, plan: clone(source.plan), totals: budgetTotals(source.plan) } : { ...base, state: 'not_set', snapshotId: null, version: 0, sourceRevisionId: null, plan: null, totals: null }
  }
  function confirm(key: string) {
    const value = month(key)
    if (value.state === 'not_set') fail('该月尚未设置预算')
    if (value.state === 'preview') { state.bases[key] = clone(value.plan); state.snapshots[key] = { ...value, state: 'confirmed', snapshotId: newId(), version: 1 } }
    return state.snapshots[key]
  }
  function entryPage(item?: string, key?: string, unlinked = false, before?: string): B.BudgetEntryPage {
    let rows = entries().filter(e => e.bookId === bookId && e.type === 'expense' && (!key || e.date.startsWith(key))).map(entry => ({ entry, link: state.links[entry.id] || null, linkVersion: state.links[entry.id]?.version || 0 })).filter(r => !item || (!r.link?.isDeleted && r.link?.itemId === item)).filter(r => !unlinked || !r.link || r.link.isDeleted).sort((a, b) => b.entry.date.localeCompare(a.entry.date) || b.entry.id.localeCompare(a.entry.id))
    if (before) { const at = rows.findIndex(r => r.entry.id === before); if (at < 0) fail('分页游标已失效', 404); rows = rows.slice(at + 1) }
    return { schemaVersion: 1, bookId, entries: rows.slice(0, 20), nextCursor: rows.length > 20 ? rows[19].entry.id : null }
  }
  function detail(id: string, beforeRecord?: string, beforeEntry?: string): B.BudgetExecutionDetail {
    const p = project(id), page = entryPage(id, undefined, false, beforeEntry)
    let records = state.records.filter(r => r.itemId === id).reverse()
    if (beforeRecord) { const at = records.findIndex(r => r.id === beforeRecord); if (at < 0) fail('流水游标已失效', 404); records = records.slice(at + 1) }
    return { schemaVersion: 1, bookId, generatedAt: Date.now(), item: p, balance: balance(id), goals: state.goals.filter(g => g.itemId === id).map(goalView), records: records.slice(0, 20), nextRecordCursor: records.length > 20 ? records[19].id : null, entries: page.entries, nextEntryCursor: page.nextCursor }
  }
  function preview(method: string, body: object) {
    const impactToken = newId(), expiresAt = Date.now() + 600000
    previews.set(impactToken, { digest: JSON.stringify({ method, body }), expires: expiresAt, version: state.plan.version, dependency: JSON.stringify(state.snapshots) })
    return { impactToken, expiresAt }
  }
  function validatePreview(method: string, body: Record<string, unknown>) {
    const { impactToken, requestId: ignored, ...input } = body
    const p = previews.get(String(impactToken))
    if (!p || p.expires < Date.now() || p.version !== state.plan.version || p.dependency !== JSON.stringify(state.snapshots) || p.digest !== JSON.stringify({ method, body: input })) fail('预览已失效，请重新预览')
  }
  const handlers: Record<string, (...args: any[]) => unknown> = {
    plan: () => state.plan,
    previewPlan: (body: B.PreviewBudgetRevision) => ({ ...preview('plan', body), ...body, untilMonth: state.plan.revisions.find(r => r.effectiveMonth > body.effectiveMonth)?.effectiveMonth || null, confirmedMonths: Object.keys(state.snapshots).filter(m => m >= body.effectiveMonth), overriddenMonths: [], totals: budgetTotals(body.plan) }),
    savePlan: (body: B.SaveBudgetRevision) => {
      validatePreview('plan', body as unknown as Record<string, unknown>); version(state.plan.version, body.version)
      if (body.effectiveMonth < monthNow()) fail('不能改写过去月份')
      money(body.plan.plannedIncome)
      for (const i of body.plan.items) { money(i.monthlyAmount); if (state.projects[i.id] && project(i.id).kind !== i.kind) fail('用途性质不能改变') }
      const revision = { id: newId(), effectiveMonth: body.effectiveMonth, plan: clone(body.plan) }
      state.plan.revisions = [...state.plan.revisions.filter(r => r.effectiveMonth !== body.effectiveMonth), revision].sort((a, b) => a.effectiveMonth.localeCompare(b.effectiveMonth)); state.plan.version++
      for (const item of body.plan.items) state.projects[item.id] = { id: item.id, kind: item.kind, status: 'active', enabledMonth: state.projects[item.id]?.enabledMonth || body.effectiveMonth, fundVersion: state.projects[item.id]?.fundVersion || 0, configuration: item }
      return { schemaVersion: 1, bookId, version: state.plan.version, revision }
    },
    month,
    monthly: (key: string) => {
      const value = month(key)
      return value.state === 'preview' ? { ...value, state: 'not_set', sourceRevisionId: null, plan: null, totals: null } : value
    },
    saveMonthly: (key: string, body: B.SaveMonthlyBudget) => {
      if (key < monthNow()) fail('不能编辑过去月份', 422)
      version(state.snapshots[key]?.version || 0, body.version)
      if (body.plan.items.length > 200 || body.plan.groups.length > 20) fail('预算项目或分组过多', 422)
      const groupIds = new Set(body.plan.groups.map(g => g.id)), ids = new Set(body.plan.items.map(i => i.id))
      if (ids.size !== body.plan.items.length || groupIds.size !== body.plan.groups.length || body.plan.groups.some(g => !g.name.trim() || g.name.length > 24)) fail('预算分组或项目不合法', 422)
      money(body.plan.plannedIncome)
      for (const item of body.plan.items) {
        money(item.monthlyAmount)
        if (!item.name.trim() || item.name.length > 24 || !groupIds.has(item.groupId)) fail('请填写项目名称并选择分组', 422)
        if (state.projects[item.id] && state.projects[item.id].kind !== item.kind) fail('已有项目性质不能更改')
      }
      for (const entry of entries().filter(e => e.bookId === bookId && e.date.startsWith(key))) {
        const link = state.links[entry.id]
        if (link && !link.isDeleted && !ids.has(link.itemId)) handlers.removeLink(entry.id, { entryVersion: entry.version || 1, linkVersion: link.version })
      }
      state.plan.version++
      for (const item of body.plan.items) state.projects[item.id] = { id: item.id, kind: item.kind, status: 'active', enabledMonth: state.projects[item.id]?.enabledMonth || key, fundVersion: state.projects[item.id]?.fundVersion || 0, configuration: clone(item) }
      state.bases[key] = clone(body.plan)
      state.snapshots[key] = { schemaVersion: 1, bookId, month: key, planVersion: state.plan.version, state: 'confirmed', snapshotId: state.snapshots[key]?.snapshotId || newId(), sourceRevisionId: newId(), version: body.version + 1, plan: clone(body.plan), totals: budgetTotals(body.plan), override: { plannedIncome: null, itemAmounts: [] }, generatedAt: Date.now() }
      return state.snapshots[key]
    },
    deleteMonthly: (key: string, body: B.DeleteMonthlyBudget) => {
      if (key < monthNow()) fail('不能删除过去月份的预算', 422)
      const value = month(key)
      if (value.state !== 'confirmed') fail('月预算已删除或未设置')
      version(value.version, body.version)
      for (const entry of entries().filter(e => e.bookId === bookId && e.date.startsWith(key))) {
        const link = state.links[entry.id]
        if (link && !link.isDeleted) handlers.removeLink(entry.id, { entryVersion: entry.version || 1, linkVersion: link.version })
      }
      state.plan.version++
      state.snapshots[key] = { ...value, state: 'not_set', version: value.version + 1, planVersion: state.plan.version, snapshotId: null, sourceRevisionId: null, plan: null, totals: null, override: { plannedIncome: null, itemAmounts: [] }, generatedAt: Date.now() }
      return state.snapshots[key]
    },
    monthlyExecution: (key: string) => handlers.execution(key, true),
    confirm: (key: string, body: B.ConfirmBudgetMonth) => { version(state.plan.version, body.planVersion); if (month(key).sourceRevisionId !== body.sourceRevisionId) fail('来源已变化'); return confirm(key) },
    updateMonth: (key: string, body: B.UpdateBudgetMonth) => {
      if (key < monthNow()) fail('不能编辑过去月份')
      const value = month(key); version(value.version, body.version)
      const row = confirm(key)
      if (!row.plan) fail('该月尚未设置预算')
      // 从原方案重建，覆盖是完整替换，不能在上次覆盖上反复累加。
      const source = state.plan.revisions.find(r => r.id === row.sourceRevisionId)
      const base = state.bases[key] || source?.plan || row.plan!
      row.plan = clone(base); row.override = clone(body.override)
      if (body.override.plannedIncome !== null) { money(body.override.plannedIncome); row.plan.plannedIncome = body.override.plannedIncome }
      for (const change of body.override.itemAmounts) { money(change.amount); const item = row.plan.items.find(i => i.id === change.itemId) || fail('该月不存在用途'); item.monthlyAmount = change.amount }
      row.version++; row.totals = budgetTotals(row.plan); return row
    },
    previewEqualization: (body: B.PreviewBudgetEqualization) => { const window = months(body.startMonth, body.endMonth); money(body.totalAmount); return { ...preview('equalize', body), schedule: window.map((month, i) => ({ month, amount: Math.floor(body.totalAmount / window.length) + (i < body.totalAmount % window.length ? 1 : 0) })), affectedMonths: window } },
    equalize: (body: B.ApplyBudgetEqualization) => { validatePreview('equalize', body as unknown as Record<string, unknown>); const window = months(body.startMonth, body.endMonth); return { schemaVersion: 1, months: window.map((key, i) => { const row = month(key); return handlers.updateMonth(key, { version: row.version, override: { ...row.override, itemAmounts: [...row.override.itemAmounts.filter(v => v.itemId !== body.itemId), { itemId: body.itemId, amount: Math.floor(body.totalAmount / window.length) + (i < body.totalAmount % window.length ? 1 : 0) }] } }) }) } },
    projects: (after?: string) => { const active = new Set([...state.plan.revisions.filter(r => r.effectiveMonth >= monthNow()).flatMap(r => r.plan.items.map(i => i.id)), ...(month(monthNow()).plan?.items.map(i => i.id) || [])]); for (const p of Object.values(state.projects)) p.status = active.has(p.id) ? 'active' : 'archived'; const rows = Object.values(state.projects).sort((a, b) => a.id.localeCompare(b.id)).filter(p => !after || p.id > after); return { schemaVersion: 1, bookId, items: rows.slice(0, 50), nextCursor: rows.length > 50 ? rows[49].id : null } },
    detail,
    fund: (id: string, body: B.CreateBudgetFund) => {
      const p = project(id); version(p.fundVersion, body.fundVersion); money(body.amount, true)
      if (p.kind === 'expense' || !['opening', 'deposit'].includes(body.kind)) fail('该用途不能登记资金', 422)
      if (body.date > today() || body.date < p.enabledMonth + '-01') fail('资金日期不合法', 422)
      const opening = recordList(id).find(r => r.kind === 'opening')
      if ((body.kind === 'opening' && opening) || (opening && body.date < opening.date)) fail('期初或执行日期冲突')
      if (body.kind === 'opening' && recordList(id).some(r => r.date < body.date)) fail('期初不能晚于已有执行')
      if (state.records.some(r => r.id === body.id)) fail('流水 ID 已使用')
      const record: B.BudgetFundRecordResponse = { ...body, note: body.note || '', itemId: id, entryId: null, goalPeriodId: null, replacesId: null, version: 1, voidedAt: null, createdAt: Date.now(), updatedAt: Date.now(), createdBy: 'demo', updatedBy: 'demo' }
      state.records.push(record); p.fundVersion++
      if (month(body.date.slice(0, 7)).plan?.items.some(i => i.id === id)) confirm(body.date.slice(0, 7))
      return { schemaVersion: 1, bookId, itemId: id, fundVersion: p.fundVersion, record, replacement: null, balance: balance(id) }
    },
    correctFund: (id: string, recordId: string, body: B.CorrectBudgetFund) => {
      const p = project(id); version(p.fundVersion, body.fundVersion)
      const record = state.records.find(r => r.id === recordId && r.itemId === id) || fail('流水不存在', 404)
      version(record.version, body.recordVersion); if (record.voidedAt || record.kind === 'use') fail('该记录不能直接修正')
      if (!body.replacement && record.kind === 'opening' && recordList(id).length > 1) fail('已有执行不能移除期初')
      record.voidedAt = Date.now(); record.version++
      let replacement = null
      if (body.replacement) { const result = handlers.fund(id, { ...body.replacement, fundVersion: body.fundVersion, kind: record.kind }) as B.SavedBudgetFund; replacement = result.record; replacement.replacesId = record.id } else p.fundVersion++
      return { schemaVersion: 1, bookId, itemId: id, fundVersion: p.fundVersion, record, replacement, balance: balance(id) }
    },
    createGoal: (id: string, body: B.CreateBudgetGoal) => {
      const p = project(id); version(p.fundVersion, body.fundVersion); money(body.targetAmount, true); money(body.carryInAmount)
      if (p.kind !== 'saving' || body.startDate > body.targetDate || body.startDate < p.enabledMonth + '-01') fail('目标设置不合法', 422)
      if (state.goals.some(g => g.itemId === id && (g.state === 'active' || g.targetDate >= body.startDate))) fail('请先关闭原目标，再按顺序续建')
      const deposits = recordList(id).filter(r => r.kind === 'deposit' && r.date >= body.startDate && r.date <= body.targetDate).reduce((n, r) => n + r.amount, 0)
      if (body.carryInAmount > Math.max(balance(id).availableAmount - deposits, 0)) fail('结转超过实际可分配资金')
      const goal: B.BudgetGoalResponse = { ...body, itemId: id, state: 'active', version: 1, closedAt: null, collectedAmount: 0, usedAmount: 0, availableAmount: 0, forecast: { fundedAmount: 0, gapAmount: body.targetAmount, surplusAmount: 0, remainingMonths: 0, overdue: false, additionalSchedule: [] } }
      state.goals.push(goal); p.fundVersion++
      return { schemaVersion: 1, bookId, itemId: id, fundVersion: p.fundVersion, goal: goalView(goal), balance: balance(id) }
    },
    updateGoal: (id: string, goalId: string, body: B.UpdateBudgetGoal) => {
      const p = project(id); version(p.fundVersion, body.fundVersion)
      const goal = state.goals.find(g => g.id === goalId && g.itemId === id) || fail('目标不存在', 404)
      version(goal.version, body.version); if (goal.state === 'closed') fail('目标已关闭')
      money(body.targetAmount, true)
      money(body.carryInAmount)
      if (body.startDate < p.enabledMonth + '-01' || body.targetDate < body.startDate || (body.state === 'closed' && body.targetDate > today()) || recordList(id).some(r => (r.kind === 'deposit' && r.date >= goal.startDate && r.date <= goal.targetDate && (r.date > body.targetDate || r.date < body.startDate)) || (r.kind === 'use' && r.goalPeriodId === goal.id && r.date < body.startDate))) fail('目标日期与执行冲突')
      if (state.goals.some(g => g.itemId === id && g.id !== goalId && g.targetDate >= body.startDate)) fail('目标期不能重叠或回写历史')
      const ownUsed = recordList(id).filter(r => r.kind === 'use' && r.goalPeriodId === goal.id).reduce((n, r) => n + r.amount, 0)
      const deposits = recordList(id).filter(r => r.kind === 'deposit' && r.date >= body.startDate && r.date <= body.targetDate).reduce((n, r) => n + r.amount, 0)
      if (body.carryInAmount > Math.max(balance(id).availableAmount + ownUsed - deposits, 0)) fail('结转超过实际资金')
      Object.assign(goal, body, { version: goal.version + 1, closedAt: body.state === 'closed' ? Date.now() : null }); p.fundVersion++
      return { schemaVersion: 1, bookId, itemId: id, fundVersion: p.fundVersion, goal: goalView(goal), balance: balance(id) }
    },
    fundingPreview: (id: string, goalId: string) => goalView(state.goals.find(g => g.id === goalId && g.itemId === id) || fail('目标不存在', 404)).forecast,
    link: (entryId: string) => { const entry = entries().find(e => e.id === entryId && e.bookId === bookId) || fail('账单不存在', 404); return { schemaVersion: 1, bookId, entryId, entryVersion: entry.version || 1, linkVersion: state.links[entryId]?.version || 0, link: state.links[entryId] || null, fundVersions: [], notice: null } },
    setLink: (entryId: string, body: B.SetBudgetEntryLink) => {
      const current = handlers.link(entryId) as B.BudgetEntryLinkState; version(current.entryVersion, body.entryVersion); version(current.linkVersion, body.linkVersion)
      const entry = entries().find(e => e.id === entryId)!, p = project(body.itemId)
      if (entry.type !== 'expense' || entry.date > today() || entry.date < p.enabledMonth + '-01' || (p.kind === 'expense' && !month(entry.date.slice(0, 7)).plan?.items.some(i => i.id === p.id))) fail('该账单不能关联用途', 422)
      const opening = recordList(p.id).find(r => r.kind === 'opening')
      if (opening && entry.date < opening.date) fail('用款日期早于期初', 422)
      if (body.goalPeriodId && !state.goals.some(g => g.id === body.goalPeriodId && g.itemId === p.id && entry.date >= g.startDate)) fail('目标期不合法', 422)
      if (current.link && !current.link.isDeleted && current.link.itemId === p.id && current.link.goalPeriodId === (body.goalPeriodId || null)) return current
      handlers.removeLink(entryId, { ...body, linkVersion: current.linkVersion })
      state.links[entryId] = { entryId, bookId, itemId: p.id, goalPeriodId: body.goalPeriodId || null, version: current.linkVersion + 1, isDeleted: false, updatedAt: Date.now(), updatedBy: 'demo' }; state.entryVersions[entryId] = entry.version || 1; sync(); return handlers.link(entryId)
    },
    removeLink: (entryId: string, body: B.RemoveBudgetEntryLink) => {
      const current = handlers.link(entryId) as B.BudgetEntryLinkState; version(current.entryVersion, body.entryVersion); version(current.linkVersion, body.linkVersion)
      if (current.link && !current.link.isDeleted) { const link = state.links[entryId]; link.isDeleted = true; link.version++; const old = state.records.find(r => r.entryId === entryId && r.voidedAt === null); if (old) { old.voidedAt = Date.now(); old.version++; project(old.itemId).fundVersion++ } }
      return handlers.link(entryId)
    },
    entries: (key: string, options: { itemId?: string; unlinkedOnly?: boolean; beforeEntryId?: string } = {}) => entryPage(options.itemId, key, options.unlinkedOnly, options.beforeEntryId),
    execution: (key: string, snapshotOnly = false) => {
      const budget = (snapshotOnly ? handlers.monthly(key) : month(key)) as B.BudgetMonthResponse, bills = entries().filter(e => e.bookId === bookId && e.type === 'expense' && e.date.startsWith(key)), expenseTotal = bills.reduce((n, e) => n + e.amount, 0)
      const linkedExpenseTotal = bills.filter(e => state.links[e.id] && !state.links[e.id].isDeleted).reduce((n, e) => n + e.amount, 0)
      const items = Object.values(state.projects).filter(p => budget.plan?.items.some(i => i.id === p.id) || recordList(p.id).some(r => r.date.startsWith(key)) || bills.some(e => state.links[e.id]?.itemId === p.id && !state.links[e.id].isDeleted)).map(p => {
        const item = budget.plan?.items.find(i => i.id === p.id), spent = bills.filter(e => state.links[e.id]?.itemId === p.id && !state.links[e.id].isDeleted).reduce((n, e) => n + e.amount, 0), records = recordList(p.id).filter(r => r.date.startsWith(key))
        return { itemId: p.id, kind: p.kind, name: (item || p.configuration).name, groupId: (item || p.configuration).groupId, inPlan: !!item, plannedAmount: item?.monthlyAmount || 0, expenseAmount: spent, remainingAmount: p.kind === 'expense' ? (item?.monthlyAmount || 0) - spent : null, depositedAmount: records.filter(r => r.kind === 'deposit').reduce((n, r) => n + r.amount, 0), usedAmount: records.filter(r => r.kind === 'use').reduce((n, r) => n + r.amount, 0), availableAmount: p.kind === 'expense' ? null : balance(p.id).availableAmount, fundVersion: p.fundVersion }
      })
      return { schemaVersion: 1, bookId, month: key, generatedAt: Date.now(), budget, expenseTotal, linkedExpenseTotal, unlinkedExpenseTotal: expenseTotal - linkedExpenseTotal, items }
    },
  }
  return Object.fromEntries(Object.keys(handlers).map(method => [method, async (...args: any[]) => {
    sync()
    const backup = clone(state), body = args.find(a => a && typeof a === 'object' && 'requestId' in a), digest = JSON.stringify({ method, args })
    if (body && state.mutations[body.requestId]) { const prior = state.mutations[body.requestId]; if (prior.digest !== digest) fail('重复请求内容已变化'); return clone(prior.result) }
    try {
      const result = clone(handlers[method](...args))
      if (body) state.mutations[body.requestId] = { digest, result }
      persist(state)
      return result
    } catch (cause) { state = backup; throw cause }
  }])) as BudgetApi
}
