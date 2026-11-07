<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import AppIcon from '@/components/AppIcon.vue'
import { useBudgetStore } from '@/store/budget'
import { useLedgerStore } from '@/store/ledger'
import { money, monthLabel } from '@/common/utils/format'
import { monthlyBudgetUsage } from '@/common/services/monthly-budget'
const ledger = useLedgerStore(), budget = useBudgetStore()
const pending = ref(false)
const usage = computed(() => budget.execution ? monthlyBudgetUsage(budget.execution) : null)
const configured = computed(() => budget.month?.state === 'confirmed' && !!usage.value)
const exceeded = computed(() => configured.value && (usage.value?.over || 0) > 0)
const usedPercent = computed(() => {
  if (!configured.value || !usage.value) return null
  if (!usage.value.amount) return usage.value.spent ? null : 0
  return Math.round(usage.value.spent / usage.value.amount * 1000) / 10
})
const progress = computed(() => configured.value && usage.value ? usage.value.amount ? Math.min(100, usage.value.spent / usage.value.amount * 100) : usage.value.spent ? 100 : 0 : 0)
const available = computed(() => !!budget.month && !!usage.value)
const footer = computed(() => {
  if (budget.error || budget.refreshError) return '更新失败，点击重试'
  if (!available.value) return pending.value || budget.loading ? '正在读取预算…' : '点击查看月度预算'
  if (!configured.value) return '设置预算，也可沿用上月'
  return usedPercent.value === null ? '当前支出已超出预算' : `预算已用 ${usedPercent.value}%`
})
let currentKey = '', task: Promise<unknown> | undefined
function refresh() {
  if (ledger.accountId && !ledger.cloudReady) return
  const key = `${ledger.accountId}:${ledger.activeBookId}:${ledger.selectedMonth}`
  if (task && key === currentKey) return task
  currentKey = key; pending.value = true
  const run = budget.load().finally(() => { if (task === run) { pending.value = false; task = undefined } })
  task = run; return run
}
watch(() => [ledger.accountId, ledger.activeBookId, ledger.selectedMonth, ledger.cloudReady], refresh, { immediate: true, flush: 'post' })
onShow(refresh)
function open() { uni.navigateTo({ url: '/pages/budget-overview/index' }) }
function activate() { if (budget.error || budget.refreshError) void refresh(); else open() }
</script>
<template>
  <button class="family-budget-card" :class="{ 'is-exceeded': exceeded, 'is-empty': !configured }" :aria-label="`${monthLabel(ledger.selectedMonth)}${ledger.activeBook.name}预算，${budget.error ? '点击重试' : '点击查看详情'}`" @tap="activate">
    <view class="card-heading">
      <view class="card-title"><AppIcon name="wallet" :size="30" /><text>{{ ledger.activeBook.kind === 'shared' ? '家庭预算' : '月度预算' }}</text></view>
      <text class="month-tag">{{ monthLabel(ledger.selectedMonth) }}</text>
    </view>
    <view class="total-row">
      <text class="amount-label">预算总额</text>
      <view v-if="configured && usage" class="total-amount number"><text class="currency">¥</text><text>{{ money(usage.amount) }}</text></view>
      <text v-else class="unset-amount">{{ available ? '未设置' : '—' }}</text>
    </view>
    <view class="metrics">
      <view class="metric"><text class="amount-label">已用金额</text><view class="metric-amount number"><text v-if="available" class="currency">¥</text><text>{{ available && usage ? money(usage.spent) : '—' }}</text></view></view>
      <view class="metric balance-metric"><text class="amount-label">{{ exceeded ? '超出金额' : '剩余金额' }}</text><view class="metric-amount number"><text v-if="configured" class="currency">¥</text><text>{{ configured && usage ? money(exceeded ? usage.over : usage.remaining) : '—' }}</text></view></view>
    </view>
    <view class="budget-track" aria-hidden="true"><view class="budget-fill" :style="{ width: `${progress}%` }" /></view>
    <view class="card-footer"><text class="book-name">{{ ledger.activeBook.name }}</text><view class="footer-action"><text>{{ footer }}</text><AppIcon name="chevron" :size="22" /></view></view>
  </button>
</template>
<style scoped>
.family-budget-card { display: block; width: 100%; box-sizing: border-box; margin: 22rpx 0; padding: 28rpx; border: 1rpx solid #dce6df; border-radius: 24rpx; background: #edf4ef; text-align: left; color: #203c35; line-height: 1.5; }
.family-budget-card:focus-visible { outline: 4rpx solid #285e4e; outline-offset: 5rpx; }
.card-heading,.card-title,.total-row,.card-footer,.footer-action { display: flex; align-items: center; }
.card-heading,.total-row,.card-footer { justify-content: space-between; gap: 16rpx; }
.card-title { gap: 12rpx; font-size: 27rpx; font-weight: 600; }
.month-tag { flex-shrink: 0; font-size: 21rpx; color: #536f60; background: #ffffffb3; padding: 5rpx 14rpx; border-radius: 9rpx; }
.total-row { margin: 28rpx 0 25rpx; flex-wrap: wrap; }
.amount-label { font-size: 23rpx; color: #60796b; }
.total-amount { display: flex; align-items: baseline; gap: 7rpx; min-width: 0; max-width: 100%; font-size: 48rpx; font-weight: 600; line-height: 1.2; overflow-wrap: anywhere; }
.currency { font-size: .62em; font-weight: 400; }
.unset-amount { color: #71877a; font-size: 34rpx; }
.metrics { display: flex; gap: 24rpx; padding-top: 24rpx; border-top: 1rpx solid #d6e3d9; }
.metric { flex: 1; min-width: 0; }.balance-metric { padding-left: 26rpx; border-left: 1rpx solid #d6e3d9; }
.metric-amount { display: flex; gap: 5rpx; align-items: baseline; font-size: 35rpx; font-weight: 500; line-height: 1.35; margin-top: 9rpx; }
.metric-amount > text:last-child { min-width: 0; overflow-wrap: anywhere; }
.balance-metric .metric-amount { color: #285e4e; }
.budget-track { height: 7rpx; border-radius: 7rpx; background: #d8e5dc; overflow: hidden; margin-top: 26rpx; }
.budget-fill { height: 100%; background: #568267; border-radius: 7rpx; }
.card-footer { margin-top: 16rpx; font-size: 21rpx; color: #60796b; }
.book-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.footer-action { justify-content: flex-end; gap: 6rpx; flex-shrink: 0; }
.is-exceeded { background: #fff5ef; border-color: #ecd9cd; }
.is-exceeded .balance-metric .amount-label,.is-exceeded .balance-metric .metric-amount { color: #a04f38; }
.is-exceeded .metrics,.is-exceeded .balance-metric { border-color: #ecd9cd; }
.is-exceeded .budget-track { background: #efded3; }.is-exceeded .budget-fill { background: #b56b4d; }
.is-empty { background: #fff; }.is-empty .budget-track { background: #edf1ee; }.is-empty .footer-action { color: #285e4e; }
@media (max-width: 360px) { .family-budget-card { padding: 24rpx; }.metric-amount { font-size: 32rpx; }.total-amount { font-size: 44rpx; }.footer-action { font-size: 20rpx; }.card-footer { flex-wrap: wrap; row-gap: 8rpx; } }
</style>
