<script setup lang="ts">
import { computed, ref } from 'vue'
import TopBar from '@/components/TopBar.vue'
import BottomNav from '@/components/BottomNav.vue'
import AppIcon from '@/components/AppIcon.vue'
import BookSwitcher from '@/components/BookSwitcher.vue'
import MonthPicker from '@/components/MonthPicker.vue'
import DonutChart from '@/components/DonutChart.vue'
import { useLedgerStore } from '@/store/ledger'
import { localDate, money, shiftMonth } from '@/common/format'
import type { EntryType } from '@/common/types'

const store = useLedgerStore()
store.hydrate()
const type = ref<EntryType>('expense')
const palette = ['#50836a', '#8cb09a', '#c4d6ab', '#dfc18c', '#bd9980', '#9baec5', '#afa3bf', '#c7cbc7']
const entries = computed(() => store.monthEntries.filter(entry => entry.type === type.value))
const total = computed(() => entries.value.reduce((sum, entry) => sum + entry.amount, 0))
const ranking = computed(() => {
  const amounts = new Map<string, number>()
  entries.value.forEach(entry => amounts.set(entry.categoryId, (amounts.get(entry.categoryId) || 0) + entry.amount))
  return [...amounts].sort((a, b) => b[1] - a[1]).map(([id, amount], index) => ({ category: store.categoryOf(id), amount, percent: total.value ? Math.round(amount / total.value * 1000) / 10 : 0, color: palette[index % palette.length] }))
})
const segments = computed(() => ranking.value.map(item => ({ value: item.amount, color: item.color })))
const activeDays = computed(() => new Set(entries.value.map(entry => entry.date)).size)
const daysInScope = computed(() => {
  const [year, month] = store.selectedMonth.split('-').map(Number)
  return store.selectedMonth === localDate().slice(0, 7) ? new Date().getDate() : new Date(year, month, 0).getDate()
})
const dailyAverage = computed(() => Math.round(total.value / daysInScope.value))
const trend = computed(() => {
  const [year, month] = store.selectedMonth.split('-').map(Number)
  const end = daysInScope.value
  return Array.from({ length: 7 }, (_, index) => {
    const day = end - 6 + index
    const date = localDate(new Date(year, month - 1, day))
    return { date, label: `${Number(date.slice(5, 7))}/${Number(date.slice(8))}`, amount: entries.value.filter(entry => entry.date === date).reduce((sum, entry) => sum + entry.amount, 0) }
  })
})
const trendMax = computed(() => Math.max(...trend.value.map(day => day.amount), 100))
function stepMonth(delta: number) { store.selectedMonth = shiftMonth(store.selectedMonth, delta) }
function openChat() { uni.navigateTo({ url: `/pages/assistant/index?mode=chat&month=${store.selectedMonth}` }) }
</script>

<template>
  <view class="page">
    <TopBar title="收支统计" subtitle="让每一笔收支，变得更清晰。" />
    <view class="between month-context"><view class="month-controls"><button aria-label="上个月" @tap="stepMonth(-1)"><AppIcon name="back" :size="26" /></button><MonthPicker v-model="store.selectedMonth" /><button aria-label="下个月" @tap="stepMonth(1)"><AppIcon name="chevron" :size="26" /></button></view><BookSwitcher /></view>
    <view class="type-switch"><button :class="{ active: type === 'expense' }" @tap="type = 'expense'">支出分析</button><button :class="{ active: type === 'income' }" @tap="type = 'income'">收入分析</button></view>

    <view class="analysis-panel panel">
      <view class="analysis-heading"><text class="section-title">{{ type === 'expense' ? '钱都花在哪儿了' : '每一份努力的收获' }}</text><text class="small muted">{{ entries.length }} 笔{{ type === 'expense' ? '支出' : '收入' }}</text></view>
      <DonutChart :segments="segments"><text class="donut-caption">本月{{ type === 'expense' ? '支出' : '收入' }}</text><text class="donut-total number">{{ money(total) }}</text><text class="donut-currency">人民币 / 元</text></DonutChart>
      <view class="chart-legend"><view v-for="item in ranking.slice(0, 4)" :key="item.category.id" class="legend-item"><view class="legend-dot" :style="{ background: item.color }" /><text>{{ item.category.name }}</text></view><text v-if="!ranking.length" class="small muted">本月暂无{{ type === 'expense' ? '支出' : '收入' }}记录</text></view>
      <view class="analysis-metrics"><view><text class="metric-value number">{{ money(dailyAverage) }}</text><text class="metric-caption">日均{{ type === 'expense' ? '支出' : '收入' }}</text></view><view class="analysis-divider" /><view><text class="metric-value number">{{ activeDays }}<text class="metric-unit"> 天</text></text><text class="metric-caption">有记录的日子</text></view></view>
    </view>

    <button class="chat-analysis-entry" @tap="openChat"><view class="chat-analysis-icon"><AppIcon name="leaf" :size="34" /></view><view><text class="chat-analysis-title">想知道更多？和墨记聊聊</text><text class="chat-analysis-copy">花费分布、消费明细，还能继续追问</text></view><AppIcon name="arrow" :size="28" /></button>
    <view class="section-head"><text class="section-title">收支趋势</text><text class="small muted">{{ store.selectedMonth === localDate().slice(0, 7) ? '最近七天' : '月末七天' }}</text></view>
    <view class="trend-panel panel"><view class="trend-top"><text class="small muted">{{ type === 'expense' ? '支出' : '收入' }}金额（元）</text><view class="row"><view class="legend-dot" :style="{ background: type === 'expense' ? '#82aa91' : '#c5a172' }" /><text class="small muted">{{ type === 'expense' ? '支出' : '收入' }}</text></view></view><view class="bars"><view v-for="day in trend" :key="day.date" class="bar-column"><text v-if="day.amount" class="bar-value number">{{ money(day.amount, 0) }}</text><view class="bar-space"><view class="bar" :class="{ 'income-bar': type === 'income' }" :style="{ height: `${Math.max(day.amount / trendMax * 100, 2)}%` }" /></view><text class="bar-label number">{{ day.label }}</text></view></view></view>

    <view class="section-head"><text class="section-title">分类排行</text><text class="small muted">按金额排序</text></view>
    <view class="ranking-panel panel"><view v-for="(item, index) in ranking" :key="item.category.id" class="ranking-row"><text class="rank-index number">{{ String(index + 1).padStart(2, '0') }}</text><view class="rank-icon" :style="{ background: item.category.color }"><AppIcon :name="item.category.icon" :size="34" /></view><view class="rank-main"><view class="between"><view><text class="rank-name">{{ item.category.name }}</text><text class="rank-percent number">{{ item.percent }}%</text></view><text class="rank-amount number">{{ money(item.amount) }}</text></view><view class="rank-track"><view :style="{ width: `${item.percent}%`, background: item.color }" /></view></view></view><view v-if="!ranking.length" class="empty"><text>记下一笔，看看收支的分布</text></view></view>
    <view class="footnote">每一笔，都让生活更有数</view>
    <BottomNav active="statistics" />
  </view>
</template>

<style scoped lang="scss">
.month-context { margin: 0 0 25rpx; }
.chat-analysis-entry { width: 100%; display: flex; align-items: center; gap: 19rpx; background: #e5efe6; border-radius: 23rpx; padding: 25rpx; margin-top: 22rpx; text-align: left; }
.chat-analysis-entry > view:nth-child(2) { flex: 1; }
.chat-analysis-icon { display: flex; align-items: center; justify-content: center; width: 55rpx; }
.chat-analysis-title { display: block; font-size: 26rpx; font-weight: 600; }
.chat-analysis-copy { display: block; font-size: 21rpx; color: #8ba08f; margin-top: 6rpx; }
.month-controls { display: flex; align-items: center; gap: 10rpx; }
.month-controls button { display: flex; align-items: center; padding: 8rpx 0; }
.type-switch { display: flex; padding: 7rpx; border-radius: 21rpx; background: #eaf0ec; margin-bottom: 24rpx; }
.type-switch button { flex: 1; padding: 15rpx; font-size: 26rpx; border-radius: 16rpx; color: #8b9a90; }
.type-switch button.active { background: #fff; color: #345b45; font-weight: 600; }
.analysis-panel { padding: 30rpx 30rpx 26rpx; }
.analysis-heading { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20rpx; }
.analysis-heading .section-title { font-size: 28rpx; }
.donut-caption { color: #91a094; font-size: 23rpx; }
.donut-total { font-size: 43rpx; font-weight: 500; margin: 5rpx 0; }
.donut-currency { color: #a2ada4; font-size: 18rpx; }
.chart-legend { display: flex; gap: 24rpx; flex-wrap: wrap; justify-content: center; margin: 14rpx 0 29rpx; }
.legend-item { display: flex; align-items: center; gap: 9rpx; font-size: 21rpx; color: #809087; }
.legend-dot { width: 11rpx; height: 11rpx; border-radius: 50%; margin-right: 9rpx; flex-shrink: 0; }
.analysis-metrics { border-top: 1rpx solid #edf2ee; padding-top: 24rpx; display: flex; align-items: center; }
.analysis-metrics > view { flex: 1; display: flex; align-items: center; flex-direction: column; }
.metric-value { font-size: 37rpx; line-height: 1.3; }
.metric-unit { font-size: 21rpx; }
.metric-caption { font-size: 21rpx; color: #93a095; margin-top: 6rpx; }
.analysis-metrics .analysis-divider { flex: none; width: 1rpx; height: 51rpx; background: #e5ece6; }
.trend-panel { padding: 26rpx; }
.trend-top { display: flex; justify-content: space-between; align-items: center; }
.bars { display: flex; gap: 20rpx; height: 240rpx; margin-top: 26rpx; }
.bar-column { flex: 1; display: flex; flex-direction: column; align-items: center; position: relative; padding-top: 26rpx; min-width: 0; }
.bar-space { flex: 1; display: flex; align-items: flex-end; width: 100%; justify-content: center; border-bottom: 1rpx solid #eef2ed; }
.bar { width: 28rpx; background: #9cbaa6; border-radius: 7rpx 7rpx 0 0; min-height: 3rpx; }
.income-bar { background: #c5a172; }
.bar-column:last-child .bar { background: #4e8166; }
.bar-column:last-child .income-bar { background: #b78248; }
.bar-label { color: #97a298; font-size: 20rpx; margin-top: 15rpx; }
.bar-value { position: absolute; top: 0; font-size: 17rpx; color: #708979; }
.ranking-panel { padding: 0 27rpx; }
.ranking-row { display: flex; align-items: center; gap: 17rpx; padding: 26rpx 0; border-bottom: 1rpx solid #f0f3ef; }
.ranking-row:last-child { border: 0; }
.rank-index { font-size: 22rpx; color: #a4b0a7; width: 30rpx; }
.rank-icon { width: 63rpx; height: 63rpx; display: flex; align-items: center; justify-content: center; border-radius: 19rpx; }
.rank-main { flex: 1; min-width: 0; }
.rank-name { font-size: 25rpx; }
.rank-percent { margin-left: 14rpx; color: #9da99f; font-size: 21rpx; }
.rank-amount { font-size: 28rpx; }
.rank-track { height: 6rpx; background: #f0f4ef; border-radius: 6rpx; margin-top: 13rpx; overflow: hidden; }
.rank-track view { height: 100%; border-radius: 6rpx; }
</style>
