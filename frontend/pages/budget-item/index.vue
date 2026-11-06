<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { onLoad, onShow, onHide, onUnload } from '@dcloudio/uni-app'
import TopBar from '@/components/TopBar.vue'
import CloudStatus from '@/components/CloudStatus.vue'
import { useLedgerStore } from '@/store/ledger'
import { useBudgetStore } from '@/store/budget'
import { canManageBook, canEditEntry } from '@/common/services/shared-books'
import { signedBudgetMoney } from '@/common/services/budget-overview'
import { fundFields, goalTerms, nextGoalDates, defaultCarry } from '@/common/services/budget-execution-form'
import { yuan } from '@/common/services/budget-editor'
import { money, localDate, toast } from '@/common/utils/format'
import { newId } from '@/common/utils/id'
import type { Entry } from '@/common/types'
import type * as B from '@/common/types/budget'

const ledger = useLedgerStore(), budget = useBudgetStore()
ledger.hydrate()
const id = ref(''), routeBook = ref(''), routeAccount = ref<string | null>(null), detail = ref<B.BudgetExecutionDetail | null>(null)
const loading = ref(false), saving = ref(false), message = ref(''), savedNotice = ref(''), scopeLost = ref(false)
const managed = computed(() => canManageBook(ledger.activeBook))
const locked = computed(() => loading.value || saving.value || budget.writing || budget.retryAvailable || !detail.value || scopeLost.value)
const cash = computed(() => detail.value?.item.kind !== 'expense')
const monthly = computed(() => budget.execution?.items.find(i => i.itemId === id.value))
const candidateMonth = ref(ledger.selectedMonth), candidates = ref<B.BudgetEntryPage | null>(null), selecting = ref(false), candidateBusy = ref(false)
const recordBusy = ref(false), entryBusy = ref(false)
let generation = 0, visible = false, candidateRevision = 0
let awaitingRecord: Set<string> | null = null
const fundForm = ref<{ kind: 'opening' | 'deposit'; amount: string; date: string; note: string; original: B.BudgetFundRecordResponse | null; remove: boolean; acknowledged: boolean; version: number } | null>(null)
const goalForm = ref<{ mode: 'new' | 'edit' | 'close'; original: B.BudgetGoalResponse | null; amount: string; start: string; end: string; carry: string; version: number } | null>(null)
const linkForm = ref<{ entry: Entry; state: B.BudgetEntryLinkState; goalId: string | null; remove: boolean } | null>(null)
const linkGoalOptions = computed(() => [{ id: null, label: '不指定目标期' }, ...(detail.value?.goals || []).map(g => ({ id: g.id, label: `${g.startDate} 至 ${g.targetDate}${g.state === 'closed' ? '（已关闭）' : ''}` }))])
const recordKind = { opening: '期初储备', deposit: '实际存入', use: '账单用款' }
function sameScope() { return routeAccount.value === ledger.accountId && routeBook.value === ledger.activeBookId }
function closeForms() { fundForm.value = null; goalForm.value = null; linkForm.value = null; selecting.value = false; candidates.value = null }
onLoad(options => { id.value = options?.id || ''; routeBook.value = options?.bookId || ledger.activeBookId; routeAccount.value = ledger.accountId })
onShow(() => { visible = true; void refresh() })
function hide() { visible = false; generation++; candidateRevision++; loading.value = false; candidateBusy.value = false; recordBusy.value = false; entryBusy.value = false }
onHide(hide); onUnload(hide)
watch(() => [ledger.accountId, ledger.activeBookId], () => {
  generation++; scopeLost.value = true; detail.value = null; closeForms(); awaitingRecord = null
  message.value = '账号或账本已变化，请返回总览重新进入项目'
}, { flush: 'sync' })
watch(() => budget.detail, value => { if (visible && sameScope() && value?.item.id === id.value) detail.value = value })
watch(managed, value => { if (!value && (fundForm.value || goalForm.value)) { fundForm.value = null; goalForm.value = null; message.value = '管理权限已变化，请读取最新数据'; } })
async function refresh() {
  if (scopeLost.value || !sameScope() || !id.value) { message.value = '项目入口已失效，请从预算总览重新进入'; return }
  const current = ++generation
  loading.value = true; message.value = ''
  try {
    if (ledger.accountId && !await ledger.refreshCloud()) { message.value = ledger.syncError || '请先连接账本'; return }
    if (current !== generation || !sameScope() || !visible) return
    if (!await budget.load()) { if (current === generation) detail.value = null; return }
    if (current !== generation) return
    const result = await budget.loadDetail(id.value)
    if (result && current === generation && visible) {
      detail.value = result
      if (awaitingRecord) {
        const added = ledger.entries.filter(e => e.bookId === routeBook.value && e.type === 'expense' && !awaitingRecord!.has(e.id))
        awaitingRecord = null
        savedNotice.value = added.length ? '新支出已保存，请核对并确认关联。' : '没有新增支出，也可选择已有账单关联。'
        if (added.length === 1) await prepareLink(added[0])
      }
    }
  } finally { if (current === generation) loading.value = false }
}
async function moreRecords() {
  if (recordBusy.value || !detail.value?.nextRecordCursor) return
  const current = generation, prior = detail.value
  recordBusy.value = true
  try { const value = await budget.query('detail', id.value, prior.nextRecordCursor || undefined); if (value && current === generation) detail.value = { ...value, entries: prior.entries, nextEntryCursor: prior.nextEntryCursor, records: [...prior.records, ...value.records] } }
  finally { if (current === generation) recordBusy.value = false }
}
async function moreEntries() {
  if (entryBusy.value || !detail.value?.nextEntryCursor) return
  const current = generation, prior = detail.value
  entryBusy.value = true
  try { const value = await budget.query('detail', id.value, undefined, prior.nextEntryCursor || undefined); if (value && current === generation) detail.value = { ...value, records: prior.records, nextRecordCursor: prior.nextRecordCursor, entries: [...prior.entries, ...value.entries] } }
  finally { if (current === generation) entryBusy.value = false }
}
function openFund(kind: 'opening' | 'deposit', original: B.BudgetFundRecordResponse | null = null) {
  if (locked.value || !managed.value || !detail.value) return
  message.value = ''; fundForm.value = { kind, amount: original ? yuan(original.amount) : '', date: original?.date || localDate(), note: original?.note || '', original, remove: false, acknowledged: false, version: detail.value.item.fundVersion }
}
async function submitFund() {
  const form = fundForm.value
  if (!form || locked.value || !managed.value) return
  try {
    if (!form.acknowledged) throw new Error('请先确认这是实际资金记录')
    const fields = form.remove ? null : fundFields(form.amount, form.date, form.note, localDate())
    saving.value = true; message.value = ''; const current = generation
    const result = form.original
      ? await budget.write('correctFund', id.value, form.original.id, { requestId: newId(), fundVersion: form.version, recordVersion: form.original.version, replacement: fields ? { ...fields, id: newId() } : null })
      : await budget.write('fund', id.value, { ...fields!, requestId: newId(), fundVersion: form.version, id: newId(), kind: form.kind })
    if (result && current === generation) await done('资金记录已保存')
  } catch (cause) { message.value = cause instanceof Error ? cause.message : '请检查资金记录' }
  finally { saving.value = false }
}
function openGoal(mode: 'new' | 'edit' | 'close', original: B.BudgetGoalResponse | null = null) {
  if (locked.value || !managed.value || !detail.value) return
  message.value = ''
  try {
    const last = [...detail.value.goals].sort((a, b) => b.targetDate.localeCompare(a.targetDate))[0]
    const dates = mode === 'new' ? nextGoalDates(last, localDate()) : { start: original!.startDate, end: mode === 'close' && original!.targetDate > localDate() ? localDate() : original!.targetDate }
    goalForm.value = { mode, original, amount: original ? yuan(original.targetAmount) : '', start: dates.start, end: dates.end, carry: original ? yuan(original.carryInAmount) : last ? defaultCarry(detail.value.balance.availableAmount) : '0.00', version: detail.value.item.fundVersion }
  } catch (cause) { message.value = cause instanceof Error ? cause.message : '请检查目标日期' }
}
async function submitGoal() {
  const form = goalForm.value
  if (!form || locked.value || !managed.value) return
  try {
    const terms = goalTerms(form.amount, form.start, form.end, form.carry)
    if (form.mode === 'close' && terms.targetDate > localDate()) throw new Error('关闭时请填写不晚于今天的实际结束日')
    saving.value = true; message.value = ''; const current = generation
    const result = form.original
      ? await budget.write('updateGoal', id.value, form.original.id, { ...terms, requestId: newId(), fundVersion: form.version, version: form.original.version, state: form.mode === 'close' ? 'closed' : 'active' })
      : await budget.write('createGoal', id.value, { ...terms, requestId: newId(), fundVersion: form.version, id: newId() })
    if (result && current === generation) await done(form.mode === 'close' ? '目标已关闭，资金与历史保留' : '目标已保存')
  } catch (cause) { message.value = cause instanceof Error ? cause.message : '请检查目标' }
  finally { saving.value = false }
}
const requestedGoal = ref<string | null>(null)
async function openCandidates(goalId: string | null = null) {
  if (locked.value) return
  selecting.value = true; requestedGoal.value = goalId; candidateMonth.value = ledger.selectedMonth; candidates.value = null
  await loadCandidates()
}
async function loadCandidates(more = false) {
  if (candidateBusy.value || (more && !candidates.value?.nextCursor)) return
  const current = generation, revision = ++candidateRevision
  candidateBusy.value = true; message.value = ''
  try {
    const result = await budget.query('entries', candidateMonth.value, { beforeEntryId: more ? candidates.value?.nextCursor || undefined : undefined })
    if (result && current === generation && revision === candidateRevision) candidates.value = more && candidates.value ? { ...result, entries: [...candidates.value.entries, ...result.entries] } : result
  } finally { if (current === generation && revision === candidateRevision) candidateBusy.value = false }
}
function candidateMonthChanged(value: string) { candidateRevision++; candidateBusy.value = false; candidateMonth.value = value; candidates.value = null; void loadCandidates() }
async function prepareLink(entry: Entry, remove = false) {
  if (!canEditEntry(ledger.activeBook, entry) || saving.value || budget.retryAvailable) return
  const current = generation; candidateBusy.value = true
  try {
    if (ledger.accountId && !await ledger.refreshCloud()) { message.value = ledger.syncError; return }
    if (current !== generation || !visible) return
    const state = await budget.query('link', entry.id)
    const fresh = ledger.entries.find(e => e.id === entry.id && e.bookId === routeBook.value)
    if (state && fresh && current === generation) {
      if (remove && (state.link?.isDeleted || state.link?.itemId !== id.value)) { message.value = '关联已变化，请刷新后重试'; return }
      linkForm.value = { entry: fresh, state, goalId: remove ? state.link?.goalPeriodId || null : requestedGoal.value, remove }
      selecting.value = false
    } else if (current === generation) message.value = '账单已变化，请刷新后重新选择'
  } finally { if (current === generation) candidateBusy.value = false }
}
async function submitLink() {
  const form = linkForm.value
  if (!form || locked.value || !canEditEntry(ledger.activeBook, form.entry)) return
  saving.value = true; message.value = ''; const current = generation
  try {
    const body = { requestId: newId(), entryVersion: form.state.entryVersion, linkVersion: form.state.linkVersion }
    const result = form.remove ? await budget.write('removeLink', form.entry.id, body) : await budget.write('setLink', form.entry.id, { ...body, itemId: id.value, goalPeriodId: form.goalId })
    if (result && current === generation) await done(form.remove ? '已取消关联，派生用款已恢复' : '支出已关联，只统计原账单一次')
  } finally { saving.value = false }
}
async function done(text: string) {
  closeForms(); savedNotice.value = text; toast(text)
  await refresh()
}
async function retry() {
  if (saving.value) return
  saving.value = true; const current = generation
  try { if (await budget.retry() && current === generation) await done('原请求已确认，请查看最新执行') }
  finally { saving.value = false }
}
function newBill() {
  if (locked.value) return
  awaitingRecord = new Set(ledger.entries.map(e => e.id)); requestedGoal.value = detail.value?.goals.find(g => g.state === 'active')?.id || null
  uni.navigateTo({ url: '/pages/record/index' })
}
function viewBill(entryId: string) {
  if (ledger.accountId) { uni.navigateTo({ url: `/pages/entry/index?id=${encodeURIComponent(entryId)}&bookId=${encodeURIComponent(routeBook.value)}` }); return }
  const entry = ledger.entries.find(e => e.id === entryId && e.bookId === routeBook.value)
  if (!entry) return toast('原账单已删除或移出此账本')
  if (canEditEntry(ledger.activeBook, entry)) uni.navigateTo({ url: `/pages/record/index?id=${encodeURIComponent(entryId)}` })
  else uni.showModal({ title: '支出详情', content: `${entry.date} · ${money(entry.amount)} 元\n${entry.note || entry.account}`, showCancel: false })
}
function acknowledge(event: Event) { if (fundForm.value) fundForm.value.acknowledged = Boolean((event as unknown as { detail: { value: boolean } }).detail.value) }
</script>

<template>
  <view class="page item-page">
    <TopBar :title="detail?.item.configuration.name || '预算项目详情'" subtitle="计划与实际，各有依据。" back />
    <CloudStatus />
    <text class="hint">{{ ledger.activeBook.name }} · {{ ledger.selectedMonth }}</text>
    <text v-if="!managed" class="hint">共同成员可查看，可关联自己有权编辑的支出；资金与目标由创建者管理。</text>
    <text v-if="loading" class="hint">正在读取项目与实际执行…</text>
    <view v-if="message || budget.error || budget.refreshError" class="error-box"><text>{{ message || budget.error || budget.refreshError }}</text><button v-if="budget.retryAvailable" :disabled="saving" @tap="retry">重试上次原请求</button><button v-else-if="!scopeLost" :disabled="saving || loading" @tap="closeForms(); refresh()">读取最新数据</button></view>
    <text v-if="savedNotice" class="notice">{{ savedNotice }}</text><text v-if="budget.notice" class="notice">{{ budget.notice }}</text>
    <button v-if="budget.retryAvailable && !budget.error" :disabled="saving" @tap="retry">上次响应未确认，重试原请求</button>
    <view v-if="detail && !scopeLost">
      <text v-if="detail.item.status === 'archived'" class="hint">此用途已归档，历史与资金继续保留。</text>
      <view class="summary"><text class="section-title">{{ cash ? '用途资金' : '当月支出额度' }}</text>
        <view class="fact-row"><text>{{ cash ? '本月计划存入' : '本月计划额度' }}</text><text>¥{{ money(monthly?.plannedAmount || 0) }}</text></view>
        <view v-if="!cash" class="fact-row"><text>已关联支出</text><text>¥{{ money(monthly?.expenseAmount || 0) }}</text></view>
        <view v-if="!cash" class="fact-row"><text>剩余额度</text><text>¥{{ signedBudgetMoney(monthly?.remainingAmount || 0) }}</text></view>
        <template v-else><view class="fact-row"><text>本月实际存入</text><text>¥{{ money(monthly?.depositedAmount || 0) }}</text></view><view class="fact-row"><text>有效期初</text><text>¥{{ money(detail.balance.openingAmount) }}</text></view><view class="fact-row"><text>累计实际存入</text><text>¥{{ money(detail.balance.depositedAmount) }}</text></view><view class="fact-row"><text>累计已使用</text><text>¥{{ money(detail.balance.usedAmount) }}</text></view><view class="fact-row strong" :class="{ warning: detail.balance.availableAmount < 0 }"><text>截至 {{ detail.balance.asOfDate }} 可用</text><text>¥{{ signedBudgetMoney(detail.balance.availableAmount) }}</text></view></template>
        <text class="hint">{{ cash ? '计划不代表已存入；可用资金由有效实际记录计算，可能为负，不代表银行账户余额。' : '额度只对照关联支出，不代表账户余额。' }}</text>
      </view>
      <view class="actions"><button v-if="cash && managed" :disabled="locked" @tap="openFund('deposit')">记录实际存入</button><button v-if="cash && managed && !detail.balance.openingAmount" :disabled="locked" @tap="openFund('opening')">登记已有储备</button><button :disabled="locked" @tap="openCandidates()">关联已有支出</button><button :disabled="locked" @tap="newBill">正常记账后关联</button></view>
      <view v-if="detail.item.kind === 'saving'" class="section"><text class="section-title">储蓄目标与历史</text>
        <text v-if="!detail.goals.length" class="hint">尚未设置目标。定存也可以不设截止目标。</text>
        <view v-for="goal in detail.goals" :key="goal.id" class="goal-card"><view class="between"><text>{{ goal.startDate }} 至 {{ goal.targetDate }}</text><text>{{ goal.state === 'closed' ? '已关闭' : '活动目标' }}</text></view>
          <view class="fact-row"><text>目标金额</text><text>¥{{ money(goal.targetAmount) }}</text></view><view class="fact-row"><text>本期已筹集</text><text>¥{{ money(goal.collectedAmount) }}</text></view><view class="fact-row"><text>已用于本期目标</text><text>¥{{ money(goal.usedAmount) }}</text></view><view class="fact-row"><text>本期可用</text><text>¥{{ signedBudgetMoney(goal.availableAmount) }}</text></view><view class="fact-row"><text>{{ goal.forecast.gapAmount ? '实际资金缺口' : '超出目标' }}</text><text>¥{{ money(goal.forecast.gapAmount || goal.forecast.surplusAmount) }}</text></view>
          <text class="hint">{{ goal.state === 'closed' ? '关闭不表示已付款；真实账单修正仍会更新历史。' : goal.forecast.overdue ? '已过截止日，仍有缺口。' : '已用金额仍保留筹集进度，付款后不会要求重攒同一笔钱。' }}</text>
          <view class="actions"><button :disabled="locked" @tap="openCandidates(goal.id)">关联此期用款</button><button v-if="managed && goal.state === 'active'" :disabled="locked" @tap="openGoal('edit', goal)">修改目标</button><button v-if="managed && goal.state === 'active'" :disabled="locked" @tap="openGoal('close', goal)">关闭本期</button></view>
        </view>
        <text v-if="detail.goals.length" class="hint">不同目标期的历史余款可能衔接同一笔钱，不能相加当成项目余额。续建只分配余款，不产生存入。</text>
        <button v-if="managed && detail.item.status === 'active' && !detail.goals.some(g => g.state === 'active')" class="text-button" :disabled="locked" @tap="openGoal('new')">{{ detail.goals.length ? '续建下一期目标' : '建立储蓄目标' }}</button>
      </view>
      <view class="section"><text class="section-title">关联支出账单</text><text class="hint">跨月份列出当前有效关联，金额只统计原账单一次。</text>
        <text v-if="!detail.entries.length" class="hint">尚未关联支出，不据此断言现实中尚未付款。</text>
        <view v-for="row in detail.entries" :key="row.entry.id" class="history-row"><button class="bill-button" @tap="viewBill(row.entry.id)"><view><text>{{ row.entry.note || ledger.categoryOf(row.entry.categoryId).name }}</text><text class="hint">{{ row.entry.date }} · 查看原账单</text></view><text>¥{{ money(row.entry.amount) }}</text></button><button v-if="canEditEntry(ledger.activeBook, row.entry)" class="text-button" :disabled="locked || candidateBusy" @tap="prepareLink(row.entry, true)">取消关联</button></view>
        <button v-if="detail.nextEntryCursor" :disabled="entryBusy || locked" @tap="moreEntries">加载更多关联账单</button>
      </view>
      <view v-if="cash" class="section"><text class="section-title">资金执行与修正记录</text><text v-if="!detail.records.length" class="hint">暂无资金记录，计划不会自动计为已存。</text>
        <view v-for="record in detail.records" :key="record.id" class="history-row" :class="{ voided: record.voidedAt !== null }"><view class="between"><text>{{ recordKind[record.kind] }}{{ record.voidedAt !== null ? '（已作废）' : '' }}</text><text>{{ record.kind === 'use' ? '−' : '+' }}¥{{ money(record.amount) }}</text></view><text class="hint">{{ record.date }} · {{ record.note || '无备注' }}</text><text v-if="record.replacesId" class="hint">替代原记录 {{ record.replacesId }}</text><button v-if="record.kind === 'use' && record.entryId" class="text-button" @tap="viewBill(record.entryId)">查看或修正原账单</button><button v-else-if="record.voidedAt === null && managed" class="text-button" :disabled="locked" @tap="openFund(record.kind as 'opening' | 'deposit', record)">修正或作废</button></view>
        <button v-if="detail.nextRecordCursor" :disabled="recordBusy || locked" @tap="moreRecords">加载更多资金记录</button>
      </view>
    </view>
    <view v-if="fundForm || goalForm || linkForm || selecting" class="sheet-mask"><scroll-view scroll-y class="execution-sheet">
      <view v-if="fundForm"><text class="section-title">{{ fundForm.original ? '修正资金记录' : fundForm.kind === 'opening' ? '登记实际已有储备' : '记录实际存入' }}</text><text class="hint">只登记已发生的用途资金，不创建收入或消费账单。</text>
        <view v-if="fundForm.original" class="actions"><button :disabled="locked" :class="{ chosen: !fundForm.remove }" @tap="fundForm.remove = false">替代修正</button><button :disabled="locked" :class="{ chosen: fundForm.remove }" @tap="fundForm.remove = true">仅作废原记录</button></view>
        <template v-if="!fundForm.remove"><view class="field-row"><text>实际金额（元）</text><input v-model="fundForm.amount" type="digit" maxlength="10" :disabled="locked" placeholder="请输入实际金额" /></view><view class="field-row"><text>实际日期</text><picker mode="date" :value="fundForm.date" :end="localDate()" :disabled="locked" @change="fundForm.date = $event.detail.value"><view>{{ fundForm.date }} ▾</view></picker></view><input v-model="fundForm.note" class="note-input" maxlength="80" :disabled="locked" placeholder="备注（可选）" /></template>
        <text v-else class="hint">原记录会保留作废痕迹，余额和目标进度会按有效记录重算。有其他执行时不能直接移除期初。</text>
        <view class="field-row"><text>确认这是实际资金记录的登记或修正</text><switch :checked="fundForm.acknowledged" :disabled="locked" color="#285e4e" @change="acknowledge" /></view><button class="primary" :disabled="locked || !fundForm.acknowledged" :loading="saving" @tap="submitFund">{{ fundForm.remove ? '确认作废记录' : '确认保存资金记录' }}</button>
      </view>
      <view v-if="goalForm"><text class="section-title">{{ goalForm.mode === 'close' ? '关闭本期目标' : goalForm.mode === 'edit' ? '修改目标' : detail?.goals.length ? '续建下一期目标' : '建立储蓄目标' }}</text><text class="hint">关闭不会清空资金，也不表示已付款；续建仅分配已有余款。</text>
        <view class="field-row"><text>目标金额（元）</text><input v-model="goalForm.amount" type="digit" maxlength="10" :disabled="locked || goalForm.mode === 'close'" placeholder="例如 50000" /></view>
        <view class="field-row"><text>开始日期</text><picker mode="date" :value="goalForm.start" :disabled="locked || goalForm.mode === 'close'" @change="goalForm.start = $event.detail.value"><view>{{ goalForm.start }} ▾</view></picker></view><view class="field-row"><text>{{ goalForm.mode === 'close' ? '实际结束日' : '截止日期' }}</text><picker mode="date" :value="goalForm.end" :disabled="locked" @change="goalForm.end = $event.detail.value"><view>{{ goalForm.end }} ▾</view></picker></view>
        <view class="field-row"><text>分配已有资金（元）</text><input v-model="goalForm.carry" type="digit" maxlength="10" :disabled="locked || goalForm.mode === 'close'" /></view><text class="hint">本期已登记存入不能再次作为结转；可分配额度由实际资金校验。</text><button class="primary" :disabled="locked" :loading="saving" @tap="submitGoal">{{ goalForm.mode === 'close' ? '确认关闭并保留历史' : '确认保存目标' }}</button>
      </view>
      <view v-if="selecting"><text class="section-title">选择实际支出账单</text><picker mode="date" fields="month" :value="candidateMonth" :disabled="candidateBusy || locked" @change="candidateMonthChanged($event.detail.value)"><view class="text-button">{{ candidateMonth }} ▾</view></picker><text class="hint">一笔支出只能关联一个用途，可选择其他月份；只能处理有权编辑的账单。</text><text v-if="candidateBusy" class="hint">正在读取账单…</text><text v-if="candidates && !candidates.entries.length" class="hint">该月没有可选支出，可先正常记账再返回。</text><view v-for="row in candidates?.entries || []" :key="row.entry.id" class="candidate-row"><button class="bill-button" :disabled="!canEditEntry(ledger.activeBook, row.entry) || candidateBusy || locked" @tap="prepareLink(row.entry)"><view><text>{{ row.entry.note || ledger.categoryOf(row.entry.categoryId).name }}</text><text class="hint">{{ row.entry.date }}{{ row.link && !row.link.isDeleted ? ' · 已有关联，选择后需确认替换' : '' }}</text></view><text>¥{{ money(row.entry.amount) }}</text></button></view><button v-if="candidates?.nextCursor" :disabled="candidateBusy" @tap="loadCandidates(true)">加载更多支出</button></view>
      <view v-if="linkForm"><text class="section-title">{{ linkForm.remove ? '确认取消关联' : '确认关联这笔支出' }}</text><text class="hint">{{ linkForm.entry.date }} · {{ linkForm.entry.note || linkForm.entry.account }}</text><view class="fact-row"><text>原账单金额</text><text>¥{{ money(linkForm.entry.amount) }}</text></view><text v-if="!linkForm.remove && linkForm.state.link && !linkForm.state.link.isDeleted && linkForm.state.link.itemId !== id" class="warning">此操作将替换原用途关联，并同步恢复原资金用途的扣减。</text><text v-if="linkForm.remove" class="hint">只取消预算归集，保留真实支出账单；对应派生用款会恢复。</text>
        <view v-if="detail?.item.kind === 'saving' && !linkForm.remove" class="field-row"><text>用款目标期</text><picker :range="linkGoalOptions" range-key="label" :value="linkGoalOptions.findIndex(g => g.id === linkForm?.goalId)" :disabled="locked" @change="linkForm.goalId = linkGoalOptions[Number($event.detail.value)].id"><view>{{ linkGoalOptions.find(g => g.id === linkForm?.goalId)?.label || '选择目标期' }} ▾</view></picker></view>
        <text class="hint">金额来自原账单，关联不会另建一笔支出。用于还贷目标时请明确选择该期；不指定目标则只扣用途资金。付款可晚于目标截止日。</text><button class="primary" :disabled="locked" :loading="saving" @tap="submitLink">{{ linkForm.remove ? '确认取消并恢复用款' : '确认关联原支出' }}</button>
      </view>
      <text v-if="message || budget.error || budget.refreshError" class="warning">{{ message || budget.error || budget.refreshError }}</text><button v-if="budget.retryAvailable" class="text-button" :disabled="saving" @tap="retry">响应未确认，重试原请求</button><button v-else-if="budget.conflict" class="text-button" :disabled="saving" @tap="closeForms(); refresh()">放弃旧表单，读取最新版本</button><button class="close-button" :disabled="saving || budget.writing || budget.retryAvailable" @tap="closeForms()">关闭／取消</button>
    </scroll-view></view>
  </view>
</template>

<style scoped lang="scss">
.item-page { padding-bottom: calc(45rpx + env(safe-area-inset-bottom)); }.hint,.notice { display: block; color: #788b80; font-size: 23rpx; line-height: 1.8; margin: 12rpx 0; overflow-wrap: anywhere; }.notice { color: #285e4e; background: #e7f0e9; padding: 18rpx; border-radius: 10rpx; }.section-title { display: block; font-size: 29rpx; font-weight: 600; margin: 14rpx 0 22rpx; }.summary { padding: 26rpx; background: #e5efe8; border-radius: 20rpx; margin: 24rpx 0; }.fact-row,.field-row { display: flex; align-items: center; justify-content: space-between; gap: 18rpx; font-size: 25rpx; padding: 13rpx 0; }.fact-row > text:first-child { color: #6d8274; }.strong { font-size: 29rpx; border-top: 1rpx solid #cbdcd0; margin-top: 12rpx; padding-top: 22rpx; }.actions { display: flex; flex-wrap: wrap; gap: 14rpx; margin: 20rpx 0; }.actions button { color: #285e4e; background: #eef3ef; border: 1rpx solid #cbdcd0; border-radius: 12rpx; padding: 20rpx 17rpx; font-size: 24rpx; }.section { border-top: 1rpx solid #dce5df; padding-top: 20rpx; margin-top: 30rpx; }.goal-card { background: #fff; border-radius: 15rpx; padding: 24rpx; margin: 20rpx 0; }.goal-card .between { flex-wrap: wrap; font-size: 24rpx; gap: 10rpx; }.text-button { text-align: left; color: #285e4e; font-size: 24rpx; padding: 18rpx 0; }.history-row { padding: 24rpx 0; border-bottom: 1rpx solid #dfe8e1; font-size: 25rpx; }.history-row .between { gap: 16rpx; flex-wrap: wrap; }.voided { color: #8d9590; }.voided > .between { text-decoration: line-through; }.bill-button { display: flex; justify-content: space-between; align-items: center; gap: 18rpx; width: 100%; text-align: left; font-size: 26rpx; }.bill-button > view { min-width: 0; flex: 1; overflow-wrap: anywhere; }.bill-button > text { flex-shrink: 0; }.error-box { padding: 24rpx; background: #f9eee8; border-radius: 12rpx; color: #9b4c38; font-size: 25rpx; line-height: 1.8; }.error-box button { margin-top: 14rpx; text-align: left; }.execution-sheet { max-height: 86vh; background: #f6f8f7; padding: 28rpx 30rpx calc(30rpx + env(safe-area-inset-bottom)); border-radius: 24rpx 24rpx 0 0; box-sizing: border-box; width: 100%; }.field-row input { width: 230rpx; min-width: 0; text-align: right; border-bottom: 1rpx solid #dce5df; height: 60rpx; font-size: 27rpx; }.field-row picker { max-width: 62%; overflow-wrap: anywhere; text-align: right; }.note-input { height: 70rpx; font-size: 25rpx; border-bottom: 1rpx solid #dce5df; }.primary { background: #285e4e; color: #fff; width: 100%; padding: 24rpx; border-radius: 14rpx; font-size: 27rpx; margin-top: 22rpx; }.close-button { padding: 25rpx; font-size: 25rpx; width: 100%; }.warning { color: #9b4c38; display: block; font-size: 24rpx; line-height: 1.8; margin: 15rpx 0; }.chosen { font-weight: 600; border-color: #285e4e !important; }.candidate-row { border-bottom: 1rpx solid #dce5df; padding: 18rpx 0; }button[disabled] { opacity: .5; }
</style>
