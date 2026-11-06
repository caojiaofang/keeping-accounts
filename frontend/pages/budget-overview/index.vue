<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { onShow, onPullDownRefresh } from '@dcloudio/uni-app'
import TopBar from '@/components/TopBar.vue'
import CloudStatus from '@/components/CloudStatus.vue'
import MonthPicker from '@/components/MonthPicker.vue'
import BookSwitcher from '@/components/BookSwitcher.vue'
import EntryList from '@/components/EntryList.vue'
import PromptSheet from '@/components/PromptSheet.vue'
import { useLedgerStore } from '@/store/ledger'
import { useBudgetStore } from '@/store/budget'
import { canManageBook } from '@/common/services/shared-books'
import { overviewGroups, monthlyDueDates, signedBudgetMoney } from '@/common/services/budget-overview'
import { money, localDate, toast } from '@/common/utils/format'
import { cents } from '@/common/services/budget-editor'
import type { BudgetEntryPage, BudgetGoalResponse, BudgetProjectPage } from '@/common/types/budget'

const ledger = useLedgerStore(), budget = useBudgetStore()
ledger.hydrate()
const loading = ref(false), extraError = ref(''), expanded = ref<string[]>([]), entries = ref<BudgetEntryPage | null>(null)
const entryLoading = ref(false), goals = ref<BudgetGoalResponse[]>([]), legacyOpen = ref(false), legacyVersion = ref<number>()
const projectPage = ref<BudgetProjectPage | null>(null), showProjects = ref(false), projectLoading = ref(false)
const managed = computed(() => canManageBook(ledger.activeBook))
const view = computed(() => budget.execution)
const plan = computed(() => view.value?.budget.plan || null)
const totals = computed(() => view.value?.budget.totals || null)
const groups = computed(() => view.value ? overviewGroups(view.value) : [])
const dueDates = computed(() => monthlyDueDates(plan.value, ledger.selectedMonth))
const future = computed(() => ledger.selectedMonth > localDate().slice(0, 7))
let generation = 0
function toggle(id: string) { expanded.value = expanded.value.includes(id) ? expanded.value.filter(v => v !== id) : [...expanded.value, id] }
function settings(mode: 'month' | 'future' = 'future') { uni.navigateTo({ url: `/pages/budget-settings/index?mode=${mode}` }) }
function openItem(id: string) { uni.navigateTo({ url: `/pages/budget-item/index?id=${encodeURIComponent(id)}&bookId=${encodeURIComponent(ledger.activeBookId)}` }) }
async function moreProjects() {
  if (projectLoading.value || !projectPage.value?.nextCursor) return
  const current = generation, prior = projectPage.value
  projectLoading.value = true
  try { const value = await budget.query('projects', prior.nextCursor || undefined); if (value && current === generation) projectPage.value = { ...value, items: [...prior.items, ...value.items] } }
  finally { if (current === generation) projectLoading.value = false }
}
async function loadEntries(more = false) {
  if (entryLoading.value || (more && !entries.value?.nextCursor)) return
  const current = generation, before = more ? entries.value?.nextCursor || undefined : undefined
  entryLoading.value = true
  try {
    const value = await budget.query('entries', ledger.selectedMonth, { unlinkedOnly: true, beforeEntryId: before })
    if (current !== generation) return
    if (value) entries.value = more && entries.value ? { ...value, entries: [...entries.value.entries, ...value.entries] } : value
    else extraError.value = '未关联账单暂时无法读取，请刷新重试'
  } finally { if (current === generation) entryLoading.value = false }
}
async function loadGoals(current: number) {
  const ids = view.value?.items.filter(i => i.kind === 'saving').map(i => i.itemId) || []
  const result: BudgetGoalResponse[] = []
  let next = 0, failed = false
  // 限制并发，不一次发出整个家庭的所有目标请求。
  await Promise.all(Array.from({ length: Math.min(4, ids.length) }, async () => {
    while (next < ids.length && current === generation) {
      const id = ids[next++]
      const detail = await budget.query('detail', id)
      if (detail) result.push(...detail.goals.filter(g => g.state === 'active'))
      else failed = true
    }
  }))
  if (current === generation) {
    goals.value = result.sort((a, b) => a.targetDate.localeCompare(b.targetDate))
    if (failed) extraError.value = '部分目标暂时无法读取，请刷新重试'
  }
}
async function refresh() {
  const current = ++generation
  loading.value = true; extraError.value = ''; entries.value = null; goals.value = []; entryLoading.value = false; projectPage.value = null; projectLoading.value = false
  try {
    if (ledger.accountId && !await ledger.refreshCloud()) { if (current === generation) extraError.value = ledger.syncError || '账本尚未连接'; return }
    if (current !== generation || !await budget.load() || current !== generation) return
    projectPage.value = budget.projects
    await Promise.all([loadEntries(), loadGoals(current)])
  } finally { if (current === generation) loading.value = false }
}
watch(() => [ledger.accountId, ledger.activeBookId, ledger.selectedMonth], () => { expanded.value = []; void refresh() }, { flush: 'post' })
onShow(refresh)
onPullDownRefresh(async () => { try { await refresh() } finally { uni.stopPullDownRefresh() } })
function openLegacy() { legacyVersion.value = ledger.activeBook.version; legacyOpen.value = true }
async function saveLegacy(value: string) {
  try { if (await ledger.updateBudget(cents(value, true), legacyVersion.value)) { legacyOpen.value = false; toast('月支出预算已更新') } }
  catch (cause) { toast(cause instanceof Error ? cause.message : '请输入有效金额') }
}
</script>

<template>
  <view class="page overview-page">
    <TopBar title="家庭预算" subtitle="计划有安排，执行看实际。" back />
    <CloudStatus />
    <view class="context-row"><MonthPicker v-model="ledger.selectedMonth" /><BookSwitcher /></view>
    <view v-if="!ledger.accountId" class="hint">本地体验，与登录账号的预算分别保存。</view>
    <view v-if="!managed" class="hint">共同成员可查看；预算方案、储备和目标由创建者管理。</view>
    <view v-if="loading" class="hint">正在读取 {{ ledger.selectedMonth }} 的预算与执行…</view>
    <view v-if="budget.error || extraError" class="error-box"><text>{{ extraError || budget.error }}</text><button :disabled="loading" @tap="refresh">刷新重试</button></view>
    <view v-if="view">
      <view v-if="!plan" class="empty-budget">
        <text class="section-title">该月未设置家庭预算</text><text class="hint">{{ budget.plan?.revisions.length ? '已安排的其他月份不会自动套用到本月。' : '可以提前安排缴费、年度预留和储蓄。' }}</text>
        <view class="legacy-row"><text>原月支出预算</text><text>¥{{ money(ledger.activeBook.budget) }}</text></view>
        <view class="legacy-row"><text>{{ ledger.activeBook.budget - view.expenseTotal < 0 ? '已超预算' : '还可支出' }}</text><text>¥{{ money(ledger.activeBook.budget - view.expenseTotal) }}</text></view>
        <button v-if="managed" class="text-button" @tap="openLegacy">调整原月支出预算</button>
        <button v-if="managed" class="primary" @tap="settings()">设置家庭预算</button><text v-else class="hint">可请账本创建者设置家庭预算。</text>
      </view>
      <view v-if="totals" class="plan-summary">
        <view class="heading"><text class="section-title">{{ ledger.selectedMonth }} 的计划分配</text><text class="state">{{ view.budget.state === 'confirmed' ? '已确认计划' : future ? '未来月份预览' : '方案预览' }}</text></view>
        <text v-if="view.budget.state === 'preview'" class="hint">预览不会确认预算，也不会登记已存或已付款。</text>
        <view class="plan-metrics"><view><text>计划收入</text><text>¥{{ money(totals.plannedIncome) }}</text></view><view><text>已分配</text><text>¥{{ money(totals.totalAllocated) }}</text></view><view :class="{ warning: totals.unallocated < 0 }"><text>{{ totals.unallocated < 0 ? '超出计划收入' : '待分配' }}</text><text>¥{{ money(totals.unallocated) }}</text></view></view>
        <text class="hint">已分配包含支出额度、预留和储蓄计划；分配完不代表账户没钱。</text>
        <view v-if="managed" class="edit-actions"><button :disabled="ledger.selectedMonth < localDate().slice(0, 7)" @tap="settings('month')">仅调整此月</button><button :disabled="ledger.selectedMonth < localDate().slice(0, 7)" @tap="settings('future')">调整后续方案</button></view>
      </view>
      <view class="execution-section"><text class="section-title">本月实际支出</text><text class="actual-total">¥{{ money(view.expenseTotal) }}</text><view class="fact-row"><text>已关联预算用途</text><text>¥{{ money(view.linkedExpenseTotal) }}</text></view><view class="fact-row"><text>尚未关联</text><text>¥{{ money(view.unlinkedExpenseTotal) }}</text></view><text class="hint">来自有效支出账单；未关联也计入消费，预留和存入不会再算一笔支出。</text></view>
      <view v-for="group in groups" :key="group.id" class="group-panel">
        <button class="group-header" :aria-expanded="expanded.includes(group.id)" @tap="toggle(group.id)"><view><text class="group-name">{{ group.name }}</text><text class="hint">计划 ¥{{ money(group.planned) }} · 已关联支出 ¥{{ money(group.expense) }}</text></view><text>{{ expanded.includes(group.id) ? '收起' : '展开' }}</text></button>
        <view v-if="expanded.includes(group.id)" class="group-items"><view v-for="item in group.items" :key="item.itemId" class="item-row">
          <view class="item-title"><text>{{ item.name }}</text><text class="kind">{{ !item.inPlan ? '本月无计划 · ' : '' }}{{ item.kind === 'expense' ? '当月支出' : item.kind === 'reserve' ? '周期预留' : '储蓄' }}</text></view>
          <view class="fact-row"><text>{{ item.kind === 'expense' ? '支出额度' : '计划存入' }}</text><text>¥{{ money(item.plannedAmount) }}</text></view>
          <template v-if="item.kind === 'expense'"><view class="fact-row"><text>已关联支出</text><text>¥{{ money(item.expenseAmount) }}</text></view><view class="fact-row" :class="{ warning: (item.remainingAmount || 0) < 0 }"><text>剩余额度</text><text>¥{{ signedBudgetMoney(item.remainingAmount || 0) }}</text></view></template>
          <template v-else><view class="fact-row"><text>本月实际存入</text><text>¥{{ money(item.depositedAmount) }}</text></view><view class="fact-row"><text>本月已使用</text><text>¥{{ money(item.usedAmount) }}</text></view><view class="fact-row" :class="{ warning: (item.availableAmount || 0) < 0 }"><text>截至今天可用资金</text><text>¥{{ signedBudgetMoney(item.availableAmount || 0) }}</text></view></template>
          <button class="text-button" @tap="openItem(item.itemId)">查看执行与记录</button>
        </view></view>
      </view>
      <text v-if="groups.some(g => g.items.some(i => i.kind !== 'expense'))" class="hint">累计可用资金截至今天，不是所选历史月的期末余额，也不代表银行账户余额。</text>
      <view class="schedule"><text class="section-title">本月约定缴费</text><view v-for="due in dueDates" :key="due.itemId" class="schedule-row"><view><text>{{ due.name }}</text><text class="hint">{{ due.date }}</text></view><text>预计 ¥{{ money(due.amount) }}</text></view><text v-if="!dueDates.length" class="hint">该月没有设置约定缴费日期。</text><text v-if="dueDates.length" class="hint">约定日期不代表已经付款，实际支付以账单为准。</text></view>
      <view v-if="goals.length" class="schedule"><text class="section-title">活动储蓄目标</text><view v-for="goal in goals" :key="goal.id" class="goal-row"><view class="item-title"><text>{{ view.items.find(i => i.itemId === goal.itemId)?.name || '储蓄目标' }}</text><text>{{ goal.targetDate }} 截止</text></view><view class="fact-row"><text>目标金额</text><text>¥{{ money(goal.targetAmount) }}</text></view><view class="fact-row"><text>实际缺口</text><text>¥{{ money(goal.forecast.gapAmount) }}</text></view><text class="hint">{{ goal.forecast.overdue ? '已过截止日，仍有资金缺口。' : '资金筹集与实际付款分别记录。' }}</text></view></view>
      <view class="schedule"><button class="text-button" @tap="showProjects = !showProjects">{{ showProjects ? '收起全部用途' : '查看全部用途（含归档）' }}</button><view v-if="showProjects"><button v-for="item in projectPage?.items || []" :key="item.id" class="project-link" @tap="openItem(item.id)"><text>{{ item.configuration.name }}</text><text>{{ item.status === 'archived' ? '已归档 · 查看历史' : '查看执行' }}</text></button><button v-if="projectPage?.nextCursor" class="text-button" :disabled="projectLoading" @tap="moreProjects">加载更多用途</button></view></view>
      <view class="unlinked-section"><text class="section-title">尚未关联的支出</text><text class="hint">不自动认领历史账单。进入具体用途后可关联已有支出。</text><EntryList v-if="entries?.entries.length" :entries="entries.entries.map(row => row.entry)" /><text v-else-if="entries" class="hint">该月没有尚未关联的支出。</text><button v-if="entries?.nextCursor" class="text-button" :disabled="entryLoading" :loading="entryLoading" @tap="loadEntries(true)">加载更多未关联账单</button></view>
      <text class="updated hint">执行更新时间 {{ new Date(view.generatedAt).toLocaleString() }}</text>
    </view>
    <PromptSheet :open="legacyOpen" title="设置原月支出预算" placeholder="预算金额（元）" :initial="String(ledger.activeBook.budget / 100)" numeric :maxlength="10" @close="legacyOpen = false" @confirm="saveLegacy" />
  </view>
</template>
<style scoped lang="scss">
.overview-page { padding-bottom: calc(45rpx + env(safe-area-inset-bottom)); }.context-row,.heading,.fact-row,.legacy-row,.item-title,.schedule-row { display: flex; justify-content: space-between; align-items: center; gap: 18rpx; }.context-row { margin: 8rpx 0 24rpx; }.hint { display: block; color: #788b80; font-size: 23rpx; line-height: 1.8; margin: 10rpx 0; }.section-title { display: block; font-size: 29rpx; font-weight: 600; }.heading { align-items: flex-start; flex-wrap: wrap; }.state,.kind { font-size: 22rpx; color: #607e6a; }.plan-summary { background: #e5efe8; padding: 28rpx; border-radius: 22rpx; margin: 20rpx 0 30rpx; }.plan-metrics { display: flex; flex-wrap: wrap; gap: 20rpx 38rpx; margin: 28rpx 0 18rpx; }.plan-metrics > view { flex: 1; min-width: 130rpx; }.plan-metrics text { display: block; font-size: 33rpx; overflow-wrap: anywhere; }.plan-metrics text:first-child { font-size: 22rpx; color: #71877a; margin-bottom: 10rpx; }.edit-actions { display: flex; gap: 20rpx; border-top: 1rpx solid #cfded3; margin-top: 22rpx; padding-top: 18rpx; }.edit-actions button { font-size: 24rpx; padding: 14rpx 0; text-align: left; color: #285e4e; flex: 1; }.execution-section { padding: 12rpx 0 26rpx; }.actual-total { display: block; font-size: 44rpx; margin: 18rpx 0; }.fact-row,.legacy-row { font-size: 25rpx; padding: 11rpx 0; }.fact-row > text:first-child { color: #71877a; }.group-panel { background: #fff; border-radius: 18rpx; margin: 20rpx 0; overflow: hidden; }.group-header { display: flex; align-items: center; justify-content: space-between; gap: 14rpx; padding: 26rpx; width: 100%; text-align: left; }.group-header > text { font-size: 22rpx; color: #285e4e; }.group-name { font-size: 29rpx; font-weight: 600; }.group-header .hint { margin-bottom: 0; }.group-items { padding: 0 26rpx; }.item-row { padding: 24rpx 0; border-top: 1rpx solid #e7eee9; }.item-title { flex-wrap: wrap; font-size: 27rpx; margin-bottom: 14rpx; }.item-title > text { overflow-wrap: anywhere; }.schedule,.unlinked-section { margin: 30rpx 0; padding-top: 26rpx; border-top: 1rpx solid #dce5df; }.schedule-row { padding: 22rpx 0; font-size: 25rpx; border-bottom: 1rpx solid #e7eee9; }.schedule-row > view { flex: 1; }.goal-row { background: #fff; padding: 24rpx; margin: 18rpx 0; border-radius: 14rpx; }.warning { color: #9b4c38; }.warning > text { color: #9b4c38; }.primary { padding: 24rpx; margin-top: 20rpx; background: #285e4e; color: #fff; border-radius: 14rpx; font-size: 27rpx; }.text-button { color: #285e4e; font-size: 25rpx; padding: 22rpx 0; text-align: left; }.empty-budget { padding: 30rpx 0; }.error-box { padding: 24rpx; background: #f9eee8; color: #9b4c38; border-radius: 14rpx; font-size: 25rpx; line-height: 1.7; }.error-box button { text-align: left; margin-top: 18rpx; }.updated { text-align: center; }button[disabled] { opacity: .5; }
.project-link { display: flex; justify-content: space-between; gap: 18rpx; padding: 24rpx 0; width: 100%; text-align: left; font-size: 26rpx; border-bottom: 1rpx solid #dce5df; }.project-link > text:last-child { color: #71877a; font-size: 23rpx; }
</style>
