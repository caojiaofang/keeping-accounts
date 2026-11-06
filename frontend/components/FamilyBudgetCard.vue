<script setup lang="ts">
import { ref, watch } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import { useBudgetStore } from '@/store/budget'
import { useLedgerStore } from '@/store/ledger'
import { money } from '@/common/utils/format'
const emit = defineEmits<{ legacy: [] }>()
const ledger = useLedgerStore(), budget = useBudgetStore()
const pending = ref(false)
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
</script>
<template>
  <view class="family-budget-card">
    <button v-if="budget.month?.totals" class="plan-card" @tap="open">
      <view class="heading"><text>家庭预算</text><text class="state">{{ budget.month.state === 'confirmed' ? '已确认计划' : '方案预览' }} · {{ ledger.selectedMonth }}</text></view>
      <view class="metrics"><view><text>计划收入</text><text>{{ money(budget.month.totals.plannedIncome) }}</text></view><view><text>已分配</text><text>{{ money(budget.month.totals.totalAllocated) }}</text></view><view><text>{{ budget.month.totals.unallocated < 0 ? '超出收入' : '待分配' }}</text><text :class="{ warning: budget.month.totals.unallocated < 0 }">{{ money(budget.month.totals.unallocated) }}</text></view></view>
      <text class="caption">含预留与储蓄计划，点击查看实际执行</text>
    </button>
    <view v-else-if="budget.month?.state === 'not_set'">
      <button class="legacy-card" @tap="emit('legacy')"><text>月支出预算</text><text>{{ ledger.activeBook.budget - ledger.expense < 0 ? '已超预算' : '还可支出' }} ¥{{ money(ledger.activeBook.budget - ledger.expense) }}</text></button>
      <button class="overview-link" @tap="open">查看家庭预算与设置</button>
    </view>
    <button v-else class="unavailable" @tap="open">{{ budget.error ? '预算读取失败，点击重试' : pending || budget.loading ? '正在读取预算…' : '查看家庭预算' }}</button>
  </view>
</template>
<style scoped>
.family-budget-card { background: #fff; border-radius: 22rpx; margin: 20rpx 0; overflow: hidden; }.plan-card { width: 100%; text-align: left; padding: 26rpx; }.heading,.legacy-card { display: flex; align-items: center; justify-content: space-between; gap: 12rpx; font-size: 27rpx; }.state,.caption { font-size: 21rpx; color: #788b80; }.metrics { display: flex; justify-content: space-between; gap: 12rpx; margin: 24rpx 0 18rpx; }.metrics > view { min-width: 0; }.metrics text { display: block; font-size: 28rpx; overflow-wrap: anywhere; }.metrics text:first-child { color: #788b80; font-size: 21rpx; margin-bottom: 8rpx; }.caption { display: block; }.warning { color: #9b4c38; }.legacy-card { padding: 26rpx; width: 100%; text-align: left; font-size: 24rpx; }.overview-link,.unavailable { width: 100%; font-size: 24rpx; padding: 22rpx; color: #285e4e; }.overview-link { border-top: 1rpx solid #edf2ee; }
</style>
