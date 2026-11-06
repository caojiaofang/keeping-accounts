<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { onShow, onLoad } from '@dcloudio/uni-app'
import TopBar from '@/components/TopBar.vue'
import CloudStatus from '@/components/CloudStatus.vue'
import { useLedgerStore } from '@/store/ledger'
import { useBudgetStore } from '@/store/budget'
import { canManageBook } from '@/common/services/shared-books'
import { createFamilyBudgetExample, budgetTotals } from '@/common/services/budget-demo'
import { fromBudgetForm, toBudgetForm, newBudgetItem, singleMonthOverride, sourcePlan, cents, yuan, periodProjection } from '@/common/services/budget-editor'
import type { BudgetForm, BudgetItemForm } from '@/common/services/budget-editor'
import type * as B from '@/common/types/budget'
import { localDate, money, toast } from '@/common/utils/format'
import { newId } from '@/common/utils/id'

const ledger = useLedgerStore(), budget = useBudgetStore()
ledger.hydrate()
const mode = ref<'future' | 'month'>('future'), effective = ref(ledger.selectedMonth)
onLoad(options => { if (options?.mode === 'month') mode.value = 'month' })
const form = ref<BudgetForm>(toBudgetForm(null)), ready = ref(false), busy = ref(false), dirty = ref(false), message = ref('')
const readonly = computed(() => !canManageBook(ledger.activeBook))
const past = computed(() => effective.value < localDate().slice(0, 7))
const locked = computed(() => readonly.value || past.value || busy.value || budget.writing || budget.retryAvailable || !ready.value)
const totals = computed(() => { try { return budgetTotals(fromBudgetForm(form.value)) } catch { return null } })
const kinds = ['当月支出', '周期费用预留', '储蓄'], kindValues: B.BudgetItemKind[] = ['expense', 'reserve', 'saving']
const cycles = ['每月', '每半年', '每年'], cycleValues = [1, 6, 12] as const
const stableIds = ref<string[]>([])
let generation = 0
type Review = { type: 'future'; body: B.SaveBudgetRevision; impact: B.BudgetImpactResponse } | { type: 'month'; plan: B.BudgetPlan; confirm: B.ConfirmBudgetMonth | null; body: B.UpdateBudgetMonth }
const review = ref<Review | null>(null)
const allocation = ref<{ itemId: string; name: string; total: string; start: string; end: string } | null>(null)
const allocationReview = ref<{ body: B.ApplyBudgetEqualization; preview: B.BudgetEqualizationPreview } | null>(null)
const goalSetup = ref<{ itemId: string; name: string; amount: string; start: string; end: string; carry: string; opening: string; openingDate: string; acknowledged: boolean; existingOpening: boolean; openingSaved: boolean; goal: B.BudgetGoalResponse | null; fundVersion: number } | null>(null)
const goalPending = ref<'opening' | 'goal' | null>(null)
function touch() { dirty.value = true; review.value = null; allocationReview.value = null; message.value = '' }
function projectionHint(item: BudgetItemForm) {
  try {
    if (item.kind === 'saving') return `按当前月额连续安排：半年 ¥${money(periodProjection(item.amount, 6).planned)}，全年 ¥${money(periodProjection(item.amount, 12).planned)}；不代表已存入，单月调整另计。`
    const value = periodProjection(item.amount, item.cycle, item.expected)
    return `按当前月额连续预留 ${item.cycle} 个月，计划 ¥${money(value.planned)}，较预计缴费${value.difference === 0 ? '持平' : (value.difference > 0 ? '多' : '少') + ' ¥' + money(value.difference)}；不代表已存入，单月调整另计。`
  } catch { return '填写有效金额后可比较固定月额计划。' }
}
function fill() {
  const source = mode.value === 'month' ? budget.month?.plan || null : sourcePlan(budget.plan?.revisions || [], effective.value)
  form.value = toBudgetForm(source)
  stableIds.value = [...new Set([...(budget.projects?.items || []).map(i => i.id), ...(budget.plan?.revisions || []).flatMap(r => r.plan.items.map(i => i.id))])]
  dirty.value = false; review.value = null; allocation.value = null; allocationReview.value = null; goalSetup.value = null
}
async function reload() {
  const current = ++generation
  ready.value = false; busy.value = true; message.value = ''
  try {
    if (ledger.accountId && !await ledger.refreshCloud()) { message.value = ledger.syncError || '请连接云端账本后重试'; return }
    if (current !== generation) return
    if (!await budget.load() || current !== generation) return
    ready.value = true; fill()
  } finally { if (current === generation) busy.value = false }
}
onShow(() => { if (!dirty.value && !budget.retryAvailable) void reload() })
watch(() => [ledger.accountId, ledger.activeBookId, ledger.selectedMonth], () => {
  generation++; ready.value = false; dirty.value = false; busy.value = false; review.value = null; allocation.value = null; goalSetup.value = null; goalPending.value = null
  effective.value = ledger.selectedMonth; form.value = toBudgetForm(null); void reload()
}, { flush: 'post' })
async function discard(): Promise<boolean> {
  if (!dirty.value) return true
  return new Promise(resolve => uni.showModal({ title: '放弃未保存的修改？', content: '已保存的预算不会改变。', confirmText: '放弃修改', success: r => resolve(r.confirm) }))
}
async function selectMode(value: 'future' | 'month') {
  if (busy.value || budget.writing || budget.retryAvailable || !ready.value || mode.value === value || !await discard()) return
  mode.value = value; fill()
}
async function selectMonth(value: string) {
  if (busy.value || budget.writing || budget.retryAvailable || !await discard()) return
  ledger.selectedMonth = value; effective.value = value
}
async function selectBook(event: { detail: { value: string | number } }) {
  if (busy.value || budget.writing || budget.retryAvailable || !await discard()) return
  ledger.selectBook(ledger.books[Number(event.detail.value)].id)
}
async function template() {
  if (locked.value || !await discard()) return
  form.value = toBudgetForm(createFamilyBudgetExample()); touch()
}
function addGroup() { if (locked.value) return; if (form.value.groups.length >= 20) return toast('最多 20 个分组'); form.value.groups.push({ id: newId(), name: '新分组' }); touch() }
function addItem(groupId: string) { if (locked.value) return; if (form.value.items.length >= 200) return toast('最多 200 个项目'); form.value.items.push(newBudgetItem(groupId, localDate())); touch() }
function removeGroup(id: string) { if (form.value.items.some(i => i.groupId === id)) return toast('请先移走或停用分组内的项目'); form.value.groups = form.value.groups.filter(g => g.id !== id); touch() }
function removeItem(id: string) {
  uni.showModal({ title: '从后续方案停用此项目？', content: '已有资金、账单和历史预算会保留。保存方案后才生效。', confirmText: '停用项目', success: r => { if (r.confirm && !locked.value) { form.value.items = form.value.items.filter(i => i.id !== id); touch() } } })
}
function setKind(item: BudgetItemForm, index: number) { if (stableIds.value.includes(item.id)) return; item.kind = kindValues[index]; item.dueDay = ''; touch() }
function moveItem(item: BudgetItemForm, index: number) { item.groupId = form.value.groups[index].id; touch() }
async function preview() {
  if (locked.value) return
  message.value = ''; busy.value = true
  const current = generation
  try {
    const plan = fromBudgetForm(form.value)
    if (mode.value === 'future') {
      const input = { version: budget.plan?.version || 0, effectiveMonth: effective.value, plan }
      const impact = await budget.query('previewPlan', input)
      if (impact && current === generation) review.value = { type: 'future', body: { ...input, requestId: newId(), impactToken: impact.impactToken }, impact }
    } else {
      const month = budget.month
      if (!month?.plan) throw new Error('该月尚无方案，请先设置从本月起的方案')
      review.value = { type: 'month', plan, confirm: month.state === 'preview' ? { requestId: newId(), sourceRevisionId: month.sourceRevisionId, planVersion: month.planVersion } : null, body: { requestId: newId(), version: month.version, override: singleMonthOverride(plan, month.plan) } }
    }
  } catch (cause) { if (current === generation) message.value = cause instanceof Error ? cause.message : '请检查表单' }
  finally { if (current === generation) busy.value = false }
}
function saved() { dirty.value = false; review.value = null; allocation.value = null; allocationReview.value = null; toast('预算已保存'); if (!budget.refreshError) fill() }
async function save() {
  if (!review.value || readonly.value || past.value || budget.writing || busy.value) return
  busy.value = true; const current = generation, frozen = review.value
  try {
    let result: unknown
    if (frozen.type === 'future') result = await budget.write('savePlan', frozen.body)
    else {
      if (frozen.confirm) {
        const confirmed = await budget.write('confirm', effective.value, frozen.confirm) as B.BudgetMonthResponse | undefined
        if (!confirmed || current !== generation) return
        frozen.body.version = confirmed.version; frozen.confirm = null
      }
      result = await budget.write('updateMonth', effective.value, frozen.body)
    }
    if (result && current === generation) saved()
  } finally { if (current === generation) busy.value = false }
}
async function retry() {
  if (busy.value) return
  busy.value = true; const current = generation, method = budget.pendingMethod
  try {
    const result = await budget.retry() as B.BudgetMonthResponse | object | undefined
    if (!result || current !== generation) return
    if (goalPending.value && goalSetup.value) { goalResult(result); return }
    // 离页后重新进入时只有原 API 请求可重放，已丢弃的单月表单不能冒充保存成功。
    if (!review.value && !allocationReview.value) {
      dirty.value = false; await reload()
      message.value = method === 'confirm' ? '上次已确认该月原计划，单月调整尚未提交，请重新填写并预览保存。' : '上次原操作已确认，请核对最新预算与实际记录。'
      return
    }
    // 月确认成功后仍需继续提交已冻结的单月覆盖，不能把确认说成编辑完成。
    if (review.value?.type === 'month' && review.value.confirm) {
      review.value.body.version = (result as B.BudgetMonthResponse).version; review.value.confirm = null
      busy.value = false; await save()
    } else saved()
  } finally { if (current === generation) busy.value = false }
}
async function resetDraft() { if (await discard()) void reload() }
function openAllocation(item: BudgetItemForm) {
  if (dirty.value) return toast('请先保存方案，再安排精确均摊')
  if (!stableIds.value.includes(item.id)) return toast('请先保存此项目')
  allocation.value = { itemId: item.id, name: item.name, total: item.expected, start: effective.value, end: effective.value }
  allocationReview.value = null
}
async function previewAllocation() {
  if (!allocation.value || locked.value) return
  busy.value = true; message.value = ''; const current = generation
  try {
    const input = { version: budget.plan?.version || 0, itemId: allocation.value.itemId, totalAmount: cents(allocation.value.total), startMonth: allocation.value.start, endMonth: allocation.value.end }
    if (input.startMonth < localDate().slice(0, 7) || input.endMonth < input.startMonth) throw new Error('请选择从本月起的有效月份范围')
    const result = await budget.query('previewEqualization', input)
    if (result && current === generation) allocationReview.value = { body: { ...input, requestId: newId(), impactToken: result.impactToken }, preview: result }
  } catch (cause) { message.value = cause instanceof Error ? cause.message : '均摊预览失败' }
  finally { if (current === generation) busy.value = false }
}
async function adoptAllocation() {
  if (!allocationReview.value || locked.value) return
  const current = generation
  if (await budget.write('equalize', allocationReview.value.body) && current === generation) saved()
}
async function openGoal(item: BudgetItemForm) {
  if (dirty.value || !stableIds.value.includes(item.id)) return toast('请先保存方案，再设置目标与已有储备')
  busy.value = true; const current = generation
  try {
    const detail = await budget.loadDetail(item.id)
    if (!detail || current !== generation) return
    const goal = detail.goals.find(g => g.state === 'active') || null
    goalSetup.value = { itemId: item.id, name: item.name, amount: goal ? yuan(goal.targetAmount) : '', start: goal?.startDate || localDate(), end: goal?.targetDate || localDate(), carry: yuan(goal?.carryInAmount || 0), opening: '', openingDate: localDate(), acknowledged: false, existingOpening: detail.balance.openingAmount > 0, openingSaved: false, goal, fundVersion: detail.item.fundVersion }
    goalPending.value = null
  } finally { if (current === generation) busy.value = false }
}
function goalResult(result: object) {
  if (!goalSetup.value) return
  goalSetup.value.fundVersion = (result as B.SavedBudgetFund).fundVersion
  if (goalPending.value === 'opening') {
    goalSetup.value.openingSaved = true; goalSetup.value.existingOpening = true
    toast('期初已保存，可继续设置目标')
  } else { goalSetup.value = null; toast('储蓄目标已保存') }
  goalPending.value = null
}
function acknowledgeOpening(event: Event) {
  if (goalSetup.value) goalSetup.value.acknowledged = Boolean((event as unknown as { detail: { value: boolean } }).detail.value)
}
async function saveOpening() {
  const g = goalSetup.value
  if (!g || locked.value || !g.acknowledged) return
  message.value = ''; busy.value = true; const current = generation
  try {
    const body: B.CreateBudgetFund = { requestId: newId(), fundVersion: g.fundVersion, id: newId(), kind: 'opening', amount: cents(g.opening, true), date: g.openingDate, note: '设置预算时确认的实际已有储备' }
    goalPending.value = 'opening'
    const result = await budget.write('fund', g.itemId, body)
    if (result && current === generation) goalResult(result)
  } catch (cause) { message.value = cause instanceof Error ? cause.message : '请检查金额' }
  finally { if (current === generation) busy.value = false }
}
async function saveGoal() {
  const g = goalSetup.value
  if (!g || locked.value) return
  message.value = ''; busy.value = true; const current = generation
  try {
    const terms = { startDate: g.start, targetDate: g.end, targetAmount: cents(g.amount, true), carryInAmount: cents(g.carry) }
    if (g.start > g.end) throw new Error('目标开始日不能晚于截止日')
    goalPending.value = 'goal'
    const result = g.goal ? await budget.write('updateGoal', g.itemId, g.goal.id, { ...terms, requestId: newId(), fundVersion: g.fundVersion, version: g.goal.version, state: 'active' }) : await budget.write('createGoal', g.itemId, { ...terms, requestId: newId(), fundVersion: g.fundVersion, id: newId() })
    if (result && current === generation) goalResult(result)
  } catch (cause) { message.value = cause instanceof Error ? cause.message : '请检查目标设置' }
  finally { if (current === generation) busy.value = false }
}
</script>

<template>
  <view class="page budget-settings">
    <TopBar title="家庭预算设置" subtitle="把每月收入，安排到具体用途。" back />
    <CloudStatus />
    <view class="context-bar">
      <picker :range="ledger.books" range-key="name" :disabled="busy || budget.writing || budget.retryAvailable" @change="selectBook"><view class="picker-value">{{ ledger.activeBook.name }} ▾</view></picker>
      <picker mode="date" fields="month" :value="effective" start="2000-01" end="2100-12" :disabled="busy || budget.writing || budget.retryAvailable" @change="selectMonth($event.detail.value)"><view class="picker-value">{{ effective }} ▾</view></picker>
    </view>
    <view v-if="!ledger.accountId" class="hint">本地体验预算，登录后不会自动上传。</view>
    <view v-if="readonly" class="hint">共同成员可查看，方案由账本创建者修改。</view>
    <view v-if="past" class="hint">过去月份保留当时预算，不能编辑。</view>
    <view class="scope-switch"><button :class="{ active: mode === 'future' }" :disabled="busy || budget.writing || budget.retryAvailable || !ready" @tap="selectMode('future')">从此月起调整</button><button :class="{ active: mode === 'month' }" :disabled="busy || budget.writing || budget.retryAvailable || !ready" @tap="selectMode('month')">仅调整此月</button></view>
    <view class="hint">{{ mode === 'month' ? '只改本月收入和金额，下月继续原方案。' : '修改后续方案；已经确认的月份与后续安排会保留。' }}</view>
    <view v-if="busy && !ready" class="status">正在读取预算…</view>
    <view v-if="message || budget.error || budget.refreshError || budget.retryAvailable" class="error-box" role="alert">
      <text>{{ message || budget.error || budget.refreshError || '上次保存响应尚未确认，请重试原请求后再继续。' }}</text>
      <button v-if="budget.retryAvailable" :disabled="busy || budget.writing" @tap="retry">重试原请求</button>
      <button v-else :disabled="busy || budget.writing" @tap="resetDraft">重新读取</button>
    </view>
    <view v-if="ready" class="form-area">
      <view class="income-section"><text class="section-title">计划月收入</text><view class="money-field"><text>¥</text><input v-model="form.income" type="digit" maxlength="10" placeholder="例如 15600" :disabled="locked" aria-label="计划月收入（元）" @input="touch" /></view><text class="hint">计划与实际收入分别记录，保存预算不会创建账单。</text></view>
      <button v-if="mode === 'future'" class="template-button" :disabled="locked" @tap="template">填入家庭示例草稿</button>
      <view v-if="!form.groups.length" class="empty">还没有分配项目。添加分组，或填入示例后调整。</view>
      <view v-for="group in form.groups" :key="group.id" class="budget-group">
        <view class="group-heading"><input v-model="group.name" maxlength="24" :disabled="locked || mode === 'month'" aria-label="分组名称" @input="touch" /><button v-if="mode === 'future'" :disabled="locked" @tap="removeGroup(group.id)">移除空分组</button></view>
        <view v-for="item in form.items.filter(i => i.groupId === group.id)" :key="item.id" class="budget-item">
          <view class="item-top"><input v-model="item.name" maxlength="24" placeholder="用途名称" :disabled="locked || mode === 'month'" aria-label="用途名称" @input="touch" /><view class="amount-input"><text>¥</text><input v-model="item.amount" type="digit" maxlength="10" placeholder="每月金额" :disabled="locked" :aria-label="`${item.name || '用途'}每月金额（元）`" @input="touch" /></view></view>
          <view class="item-options"><picker :range="kinds" :value="kindValues.indexOf(item.kind)" :disabled="locked || mode === 'month' || stableIds.includes(item.id)" @change="setKind(item, Number($event.detail.value))"><view class="kind-label">{{ kinds[kindValues.indexOf(item.kind)] }}{{ stableIds.includes(item.id) ? '' : ' ▾' }}</view></picker><picker v-if="mode === 'future'" :range="form.groups" range-key="name" :disabled="locked" @change="moveItem(item, Number($event.detail.value))"><view class="small-action">移到分组 ▾</view></picker></view>
          <view v-if="item.kind === 'expense'" class="field-row"><text>每月缴费日（可选）</text><input v-model="item.dueDay" type="number" maxlength="2" placeholder="1—31" :disabled="locked || mode === 'month'" aria-label="每月缴费日" @input="touch" /></view>
          <view v-if="item.kind === 'reserve'" class="reserve-fields">
            <view class="field-row"><text>预计缴费（元）</text><input v-model="item.expected" type="digit" maxlength="10" placeholder="例如 6100" :disabled="locked || mode === 'month'" aria-label="预计缴费金额" @input="touch" /></view>
            <view class="field-row"><text>约定缴费日</text><picker mode="date" :value="item.anchor" start="2000-01-01" end="2100-12-31" :disabled="locked || mode === 'month'" @change="item.anchor = $event.detail.value; touch()"><view>{{ item.anchor || '选择日期' }} ▾</view></picker></view>
            <view class="field-row"><text>缴费周期</text><picker :range="cycles" :value="cycleValues.indexOf(item.cycle)" :disabled="locked || mode === 'month'" @change="item.cycle = cycleValues[Number($event.detail.value)]; touch()"><view>{{ cycles[cycleValues.indexOf(item.cycle)] }} ▾</view></picker></view>
            <text class="hint">可手动预留 520 元；预计缴费与计划预留允许有差额。</text>
            <text class="hint">{{ projectionHint(item) }}</text>
            <button v-if="mode === 'future'" class="small-action" :disabled="locked" @tap="openAllocation(item)">按月份精确均摊</button>
          </view>
          <text v-if="item.kind === 'saving'" class="hint">每月计划与已存金额分开，定存也可以不设目标。</text>
          <text v-if="item.kind === 'saving'" class="hint">{{ projectionHint(item) }}</text>
          <button v-if="item.kind === 'saving' && mode === 'future'" class="small-action" :disabled="locked" @tap="openGoal(item)">设置目标与已有储备</button>
          <input v-model="item.note" class="note-input" maxlength="80" placeholder="备注（可选）" :disabled="locked || mode === 'month'" aria-label="用途备注" @input="touch" />
          <button v-if="mode === 'future'" class="remove-item" :disabled="locked" @tap="removeItem(item.id)">停用此项目</button>
        </view>
        <button v-if="mode === 'future'" class="add-item" :disabled="locked" @tap="addItem(group.id)">＋ 添加项目</button>
      </view>
      <button v-if="mode === 'future'" class="add-group" :disabled="locked" @tap="addGroup">＋ 添加分组</button>
      <view class="allocation-summary"><view><text>计划收入</text><text>{{ totals ? money(totals.plannedIncome) : '—' }}</text></view><view><text>已分配（含预留与储蓄）</text><text>{{ totals ? money(totals.totalAllocated) : '—' }}</text></view><view :class="{ warning: totals && totals.unallocated < 0 }"><text>{{ totals && totals.unallocated < 0 ? '超出计划收入' : '待分配' }}</text><text>{{ totals ? money(Math.abs(totals.unallocated)) : '—' }}</text></view><text class="hint">分配完不代表账户余额为零，实际消费与存款不会增加。</text></view>
      <view v-if="review" class="review-box">
        <text class="section-title">确认保存范围</text>
        <text v-if="review.type === 'future'">从 {{ effective }} 起，至 {{ review.impact.untilMonth ? review.impact.untilMonth + ' 前' : '下一份明确安排前' }}沿用此方案。保留 {{ review.impact.confirmedMonths.length }} 个已确认月份，其中 {{ review.impact.overriddenMonths.length }} 个有单月调整。</text>
        <text v-else>只保存 {{ effective }} 的收入和项目金额，不改变下一月。{{ review.confirm ? '保存时会先确认该月原计划。' : '' }}</text>
        <text v-if="totals && totals.unallocated < 0" class="warning">分配超出计划收入，仍可按当前安排保存。</text>
        <button class="primary" :loading="busy || budget.writing" :disabled="readonly || past || busy || budget.writing || budget.retryAvailable" @tap="save">确认保存预算</button>
        <button :disabled="busy || budget.writing" @tap="review = null">返回修改</button>
      </view>
      <view v-else class="save-actions"><button class="primary" :disabled="locked" :loading="busy || budget.writing" @tap="preview">查看影响并保存</button><button :disabled="busy || budget.writing || budget.retryAvailable" @tap="resetDraft">取消修改</button></view>
    </view>
    <view v-if="allocation" class="sheet-mask" @tap="!busy && !budget.writing && !budget.retryAvailable && (allocation = null)"><scroll-view scroll-y class="allocation-sheet" @tap.stop>
      <text class="section-title">{{ allocation.name }} · 精确均摊</text><text class="hint">只调整所选月份的预留额，默认月额与实际资金不变。</text>
      <view class="field-row"><text>总金额（元）</text><input v-model="allocation.total" type="digit" :disabled="busy || budget.writing || budget.retryAvailable" @input="allocationReview = null" /></view>
      <view class="field-row"><text>起始月份</text><picker mode="date" fields="month" :value="allocation.start" :disabled="busy || budget.writing || budget.retryAvailable" @change="allocation.start = $event.detail.value; allocationReview = null"><view>{{ allocation.start }} ▾</view></picker></view>
      <view class="field-row"><text>截止月份</text><picker mode="date" fields="month" :value="allocation.end" :disabled="busy || budget.writing || budget.retryAvailable" @change="allocation.end = $event.detail.value; allocationReview = null"><view>{{ allocation.end }} ▾</view></picker></view>
      <view v-if="allocationReview"><view v-for="row in allocationReview.preview.schedule" :key="row.month" class="field-row"><text>{{ row.month }}</text><text>{{ yuan(row.amount) }} 元</text></view><button class="primary" :disabled="locked" @tap="adoptAllocation">采用此月份安排</button></view>
      <button v-else class="primary" :disabled="locked" @tap="previewAllocation">预览均摊金额</button>
      <text v-if="message || budget.error" class="warning">{{ message || budget.error }}</text><button v-if="budget.retryAvailable" :disabled="busy || budget.writing" @tap="retry">重试原均摊请求</button>
      <button :disabled="busy || budget.writing || budget.retryAvailable" @tap="allocation = null">关闭</button>
    </scroll-view></view>
    <view v-if="goalSetup" class="sheet-mask" @tap="!busy && !budget.writing && !budget.retryAvailable && (goalSetup = null)"><scroll-view scroll-y class="allocation-sheet" @tap.stop>
      <text class="section-title">{{ goalSetup.name }} · 目标设置</text>
      <text class="hint">目标只安排用途，不会生成存款。已有储备需单独确认实际金额。</text>
      <view v-if="!goalSetup.existingOpening">
        <view class="field-row"><text>实际已有储备（元）</text><input v-model="goalSetup.opening" type="digit" maxlength="10" placeholder="没有可留空" :disabled="locked" /></view>
        <view class="field-row"><text>期初日期</text><picker mode="date" :value="goalSetup.openingDate" :disabled="locked" @change="goalSetup.openingDate = $event.detail.value"><view>{{ goalSetup.openingDate }} ▾</view></picker></view>
        <view class="field-row"><text>确认这笔资金实际已存在</text><switch :checked="goalSetup.acknowledged" :disabled="locked" color="#285e4e" @change="acknowledgeOpening" /></view>
        <button class="template-button" :disabled="locked || !goalSetup.acknowledged || !goalSetup.opening" @tap="saveOpening">单独保存实际期初</button>
      </view>
      <text v-else class="hint">{{ goalSetup.openingSaved ? '期初已单独保存，关闭此页也会保留。' : '已有期初记录，此处不会重复增加。' }}</text>
      <view class="field-row"><text>目标金额（元）</text><input v-model="goalSetup.amount" type="digit" maxlength="10" placeholder="例如 50000" :disabled="locked" /></view>
      <view class="field-row"><text>开始日期</text><picker mode="date" :value="goalSetup.start" :disabled="locked" @change="goalSetup.start = $event.detail.value"><view>{{ goalSetup.start }} ▾</view></picker></view>
      <view class="field-row"><text>截止日期</text><picker mode="date" :value="goalSetup.end" :disabled="locked" @change="goalSetup.end = $event.detail.value"><view>{{ goalSetup.end }} ▾</view></picker></view>
      <view class="field-row"><text>分配已有资金（元）</text><input v-model="goalSetup.carry" type="digit" maxlength="10" :disabled="locked" /></view>
      <text class="hint">分配不会增加现金；本期已登记存入不能再次作为期初分配。</text>
      <button class="primary" :disabled="locked" :loading="busy || budget.writing" @tap="saveGoal">{{ goalSetup.goal ? '保存目标修改' : '建立储蓄目标' }}</button>
      <text v-if="message || budget.error" class="warning">{{ message || budget.error }}</text><button v-if="budget.retryAvailable" :disabled="busy || budget.writing" @tap="retry">重试原请求</button>
      <button v-else-if="budget.conflict" :disabled="busy" @tap="goalSetup = null; reload()">关闭并读取最新版本</button>
      <button :disabled="busy || budget.writing || budget.retryAvailable" @tap="goalSetup = null">关闭</button>
    </scroll-view></view>
  </view>
</template>

<style scoped lang="scss">
.budget-settings { padding-bottom: calc(48rpx + env(safe-area-inset-bottom)); }
.context-bar, .scope-switch, .item-top, .item-options, .field-row, .group-heading { display: flex; justify-content: space-between; align-items: center; gap: 18rpx; }
.context-bar { padding: 12rpx 0 20rpx; border-bottom: 1rpx solid #dce5df; }.picker-value { padding: 14rpx 0; font-size: 27rpx; }
.hint { display: block; color: #78877f; font-size: 23rpx; line-height: 1.7; margin: 12rpx 0; }.scope-switch { margin-top: 24rpx; padding: 7rpx; background: #e7eeea; border-radius: 14rpx; gap: 5rpx; }.scope-switch button { flex: 1; font-size: 25rpx; padding: 17rpx 6rpx; border-radius: 10rpx; }.scope-switch .active { background: #fff; color: #285e4e; font-weight: 600; }
.section-title { display: block; font-size: 29rpx; font-weight: 600; margin-bottom: 18rpx; }.income-section { padding: 30rpx 0 20rpx; }.money-field { display: flex; align-items: center; gap: 16rpx; font-size: 38rpx; }.money-field input { height: 80rpx; font-size: 48rpx; width: 100%; }.template-button, .add-group { width: 100%; color: #285e4e; border: 1rpx solid #bacfc3; padding: 22rpx; border-radius: 14rpx; font-size: 26rpx; }
.budget-group { margin: 30rpx 0; }.group-heading { margin: 16rpx 0; }.group-heading input { font-size: 29rpx; font-weight: 600; min-width: 0; flex: 1; }.group-heading button { font-size: 22rpx; color: #78877f; padding: 12rpx 0; }.budget-item { background: #fff; border-bottom: 1rpx solid #e1e9e4; padding: 25rpx 22rpx; }.budget-item:first-of-type { border-radius: 16rpx 16rpx 0 0; }.item-top > input { flex: 1; min-width: 0; font-size: 28rpx; height: 62rpx; }.amount-input { display: flex; align-items: center; gap: 8rpx; max-width: 45%; }.amount-input input { width: 180rpx; text-align: right; font-size: 30rpx; height: 62rpx; }.item-options { padding: 12rpx 0; }.kind-label { font-size: 23rpx; color: #285e4e; }.small-action { color: #285e4e; font-size: 23rpx; padding: 12rpx 0; text-align: left; }.field-row { padding: 16rpx 0; font-size: 24rpx; }.field-row input { width: 220rpx; text-align: right; height: 52rpx; border-bottom: 1rpx solid #dce5df; }.note-input { font-size: 23rpx; height: 64rpx; border-bottom: 1rpx solid #edf1ee; }.remove-item { color: #8a6357; text-align: right; font-size: 22rpx; padding: 18rpx 0 0; }.add-item { background: #fff; color: #285e4e; font-size: 25rpx; padding: 24rpx; width: 100%; border-radius: 0 0 14rpx 14rpx; }
.allocation-summary { margin: 32rpx 0; padding: 26rpx 0; border-top: 2rpx solid #285e4e; }.allocation-summary > view { display: flex; justify-content: space-between; padding: 12rpx 0; font-size: 26rpx; }.allocation-summary > view:last-of-type { font-size: 30rpx; font-weight: 600; }.primary { color: #fff; background: #285e4e; width: 100%; padding: 24rpx 12rpx; border-radius: 14rpx; font-size: 28rpx; margin: 16rpx 0; }.save-actions > button:last-child { padding: 22rpx; color: #78877f; }.review-box { background: #e8f0eb; padding: 28rpx; border-radius: 16rpx; }.review-box > text { display: block; line-height: 1.8; font-size: 25rpx; }.warning { color: #9b4c38; }.error-box { background: #f9eee8; padding: 24rpx; margin: 20rpx 0; border-radius: 12rpx; font-size: 25rpx; line-height: 1.8; color: #8c4c3a; }.error-box button { padding: 18rpx 0 0; text-align: left; font-weight: 600; }.status, .empty { padding: 32rpx 0; color: #78877f; font-size: 25rpx; line-height: 1.8; }.allocation-sheet { background: #f6f8f7; padding: 35rpx 30rpx calc(30rpx + env(safe-area-inset-bottom)); border-radius: 24rpx 24rpx 0 0; max-height: 82vh; box-sizing: border-box; width: 100%; }button[disabled] { opacity: .5; }
</style>
