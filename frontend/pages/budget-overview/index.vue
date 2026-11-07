<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { onShow, onPullDownRefresh, onShareAppMessage } from '@dcloudio/uni-app'
import TopBar from '@/components/TopBar.vue'
import CloudStatus from '@/components/CloudStatus.vue'
import MonthPicker from '@/components/MonthPicker.vue'
import BookSwitcher from '@/components/BookSwitcher.vue'
import EntryList from '@/components/EntryList.vue'
import { useLedgerStore } from '@/store/ledger'
import { useBudgetStore } from '@/store/budget'
import { canWriteBook } from '@/common/services/shared-books'
import { monthlyBudgetUsage } from '@/common/services/monthly-budget'
import { money, localDate } from '@/common/utils/format'
import type { BudgetEntryPage } from '@/common/types/budget'
import { appShare } from '@/common/services/share'

onShareAppMessage(appShare)

const ledger = useLedgerStore(), budget = useBudgetStore()
ledger.hydrate()
const loading = ref(false), entryLoading = ref(false), extraError = ref(''), entries = ref<BudgetEntryPage | null>(null)
const view = computed(() => budget.execution)
const usage = computed(() => view.value ? monthlyBudgetUsage(view.value) : null)
const editable = computed(() => canWriteBook(ledger.activeBook) && ledger.selectedMonth >= localDate().slice(0, 7))
let generation = 0
function settings() { uni.navigateTo({ url: '/pages/budget-settings/index' }) }
function record(itemId = '') { uni.navigateTo({ url: `/pages/record/index?budgetItemId=${encodeURIComponent(itemId)}&bookId=${encodeURIComponent(ledger.activeBookId)}&date=${ledger.selectedMonth === localDate().slice(0, 7) ? localDate() : ledger.selectedMonth + '-01'}` }) }
function openItem(id: string) { uni.navigateTo({ url: `/pages/budget-item/index?id=${encodeURIComponent(id)}&bookId=${encodeURIComponent(ledger.activeBookId)}` }) }
async function loadEntries(more = false) {
  if (entryLoading.value || (more && !entries.value?.nextCursor)) return
  const current = generation; entryLoading.value = true
  try {
    const value = await budget.query('entries', ledger.selectedMonth, { unlinkedOnly: true, beforeEntryId: more ? entries.value?.nextCursor || undefined : undefined })
    if (current !== generation) return
    if (value) entries.value = more && entries.value ? { ...value, entries: [...entries.value.entries, ...value.entries] } : value
    else extraError.value = '未关联账单读取失败，请刷新重试'
  } finally { if (current === generation) entryLoading.value = false }
}
async function refresh() {
  const current = ++generation
  loading.value = true; extraError.value = ''; entries.value = null; entryLoading.value = false
  try {
    if (ledger.accountId && !await ledger.refreshCloud()) { extraError.value = ledger.syncError || '请连接云端账本'; return }
    if (current !== generation || !await budget.load() || current !== generation) return
    await loadEntries()
  } finally { if (current === generation) loading.value = false }
}
watch(() => [ledger.accountId, ledger.activeBookId, ledger.selectedMonth], refresh, { flush: 'post' })
onShow(refresh)
onPullDownRefresh(async () => { try { await refresh() } finally { uni.stopPullDownRefresh() } })
</script>

<template>
  <view class="page monthly-overview">
    <TopBar title="月度预算" subtitle="这个月，花到哪里，还剩多少。" back />
    <CloudStatus />
    <view class="context-row"><MonthPicker v-model="ledger.selectedMonth" /><BookSwitcher /></view>
    <text v-if="ledger.activeBook.kind === 'shared'" class="hint">家庭成员共同维护预算，消费按账单日期归入对应月份。</text>
    <text v-if="!ledger.accountId" class="hint">当前为本地体验，登录后不会自动上传。</text>
    <view v-if="budget.error || extraError || budget.refreshError" class="error-box"><text>{{ extraError || budget.error || budget.refreshError }}</text><button :disabled="loading" @tap="refresh">刷新重试</button></view>
    <text v-if="loading" class="hint">正在读取 {{ ledger.selectedMonth }} 的预算…</text>
    <view v-if="view && usage">
      <view v-if="!view.budget.plan" class="empty-budget"><text class="section-title">{{ ledger.selectedMonth }} 尚未设置预算</text><text class="hint">添加本月预算项目，也可以在设置中沿用上月额度。</text><button v-if="editable" class="primary" @tap="settings">设置本月预算</button></view>
      <view v-else class="month-summary" :class="{ exceeded: usage.over > 0 }">
        <view class="summary-heading"><text>{{ ledger.selectedMonth }} 支出预算</text><button v-if="editable" @tap="settings">修改 / 增加项目</button></view>
        <text class="budget-amount">¥{{ money(usage.amount) }}</text>
        <view class="usage-row"><view><text>本月已用</text><text>¥{{ money(usage.spent) }}</text></view><view><text>{{ usage.over > 0 ? '已超预算' : '剩余可用' }}</text><text>¥{{ money(usage.over || usage.remaining) }}</text></view></view>
        <text class="hint">本月已用包含未关联支出。项目额度只扣除关联到该项目的账单。</text>
      </view>
      <view class="section-heading"><text class="section-title">本月预算项目</text><button v-if="editable" @tap="record()">记一笔</button></view>
      <view v-for="item in view.items" :key="item.itemId" class="budget-item">
        <view class="item-heading"><text>{{ item.name }}</text><text class="hint">{{ item.inPlan ? '额度 ¥' + money(item.plannedAmount) : '已移出本月预算' }}</text></view>
        <template v-if="item.kind === 'expense'">
          <view class="usage-row"><view><text>已用</text><text>¥{{ money(item.expenseAmount) }}</text></view><view :class="{ warning: (item.remainingAmount || 0) < 0 }"><text>{{ (item.remainingAmount || 0) < 0 ? '已超支' : '剩余' }}</text><text>¥{{ money(Math.abs(item.remainingAmount || 0)) }}</text></view></view>
          <view class="progress"><view :class="{ warning: (item.remainingAmount || 0) < 0 }" :style="{ width: `${item.plannedAmount ? Math.min(100, item.expenseAmount / item.plannedAmount * 100) : item.expenseAmount ? 100 : 0}%` }" /></view>
        </template>
        <text v-else class="hint">原有{{ item.kind === 'reserve' ? '预留' : '储蓄' }}项目 · 本月已使用 ¥{{ money(item.usedAmount) }}</text>
        <view class="item-actions"><button @tap="openItem(item.itemId)">查看关联账单</button><button v-if="item.inPlan && item.kind === 'expense' && canWriteBook(ledger.activeBook)" @tap="record(item.itemId)">记入此项</button></view>
      </view>
      <view class="unlinked-section"><text class="section-title">尚未关联的支出 · ¥{{ money(view.unlinkedExpenseTotal) }}</text><text class="hint">打开账单，在“预算项目”中选择当月项目，保存后自动累计。</text><EntryList v-if="entries?.entries.length" :entries="entries.entries.map(row => row.entry)" /><text v-else-if="entries" class="hint">本月没有未关联支出。</text><button v-if="entries?.nextCursor" class="text-button" :disabled="entryLoading" @tap="loadEntries(true)">加载更多</button></view>
    </view>
  </view>
</template>

<style scoped lang="scss">
.monthly-overview { padding-bottom: calc(45rpx + env(safe-area-inset-bottom)); }.context-row,.summary-heading,.usage-row,.section-heading,.item-heading,.item-actions { display: flex; justify-content: space-between; align-items: center; gap: 22rpx; }.context-row { margin: 8rpx 0 24rpx; }.hint { display: block; color: #71877a; font-size: 23rpx; line-height: 1.8; margin: 12rpx 0; }.section-title { display: block; font-size: 29rpx; font-weight: 600; }.month-summary { background: #e5efe8; padding: 28rpx; border-radius: 20rpx; margin: 24rpx 0 36rpx; }.summary-heading { font-size: 25rpx; flex-wrap: wrap; }.summary-heading button,.section-heading button,.item-actions button,.text-button { color: #285e4e; font-size: 24rpx; padding: 16rpx 0; }.budget-amount { display: block; font-size: 48rpx; margin: 16rpx 0 28rpx; }.usage-row { padding: 12rpx 0; align-items: flex-start; }.usage-row > view { flex: 1; }.usage-row text { display: block; font-size: 29rpx; }.usage-row text:first-child { color: #71877a; font-size: 23rpx; margin-bottom: 12rpx; }.exceeded { background: #f6ebe5; }.exceeded .usage-row > view:last-child,.warning,.warning text:first-child { color: #9b4c38; }.section-heading { margin: 24rpx 0 8rpx; }.budget-item { padding: 26rpx 0 14rpx; border-bottom: 1rpx solid #dce5df; }.item-heading { font-size: 29rpx; flex-wrap: wrap; }.item-heading .hint { margin: 0; }.progress { height: 8rpx; background: #e1e9e3; margin-top: 14rpx; border-radius: 8rpx; overflow: hidden; }.progress > view { height: 100%; background: #568267; }.progress > view.warning { background: #af674e; }.item-actions { margin-top: 8rpx; }.unlinked-section { margin-top: 40rpx; }.empty-budget { padding: 30rpx 0; }.primary { background: #285e4e; color: white; padding: 25rpx; border-radius: 14rpx; font-size: 27rpx; margin-top: 24rpx; }.error-box { padding: 24rpx; background: #f9eee8; color: #9b4c38; font-size: 25rpx; line-height: 1.8; }.error-box button { text-align: left; padding-top: 18rpx; }button[disabled] { opacity: .5; }
</style>
