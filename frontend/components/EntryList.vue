<script setup lang="ts">
import { computed } from 'vue'
import type { Entry } from '@/common/types'
import { useLedgerStore } from '@/store/ledger'
import { dateLabel, money } from '@/common/format'
import AppIcon from './AppIcon.vue'
const props = defineProps<{ entries: Entry[] }>()
const store = useLedgerStore()
const groups = computed(() => {
  const dates = [...new Set(props.entries.map(entry => entry.date))].sort().reverse()
  return dates.map(date => {
    const entries = props.entries.filter(entry => entry.date === date).sort((a, b) => b.createdAt - a.createdAt)
    return { date, entries, expense: entries.filter(entry => entry.type === 'expense').reduce((sum, entry) => sum + entry.amount, 0), income: entries.filter(entry => entry.type === 'income').reduce((sum, entry) => sum + entry.amount, 0) }
  })
})
function edit(id: string) { uni.navigateTo({ url: `/pages/record/index?id=${encodeURIComponent(id)}` }) }
function add() { uni.navigateTo({ url: '/pages/record/index' }) }
</script>

<template>
  <view v-if="!groups.length" class="empty panel">
    <view class="empty-mark"><AppIcon name="leaf" :size="56" /></view>
    <view class="empty-title">这里还没有账单</view>
    <view class="small">记下一笔，让生活的收支有迹可循</view>
    <button class="secondary-button" @tap="add">记第一笔</button>
  </view>
  <view v-for="group in groups" :key="group.date" class="day-group">
    <view class="day-heading"><text>{{ dateLabel(group.date) }}</text><view class="day-totals"><text v-if="group.expense">支出 {{ money(group.expense) }}</text><text v-if="group.income">收入 {{ money(group.income) }}</text></view></view>
    <view class="entries-panel">
      <button v-for="entry in group.entries" :key="entry.id" class="entry" @tap="edit(entry.id)">
        <view class="category-icon" :style="{ background: store.categoryOf(entry.categoryId).color }"><AppIcon :name="store.categoryOf(entry.categoryId).icon" :size="39" /></view>
        <view class="entry-info"><text class="entry-category">{{ store.categoryOf(entry.categoryId).name }}</text><text class="entry-note">{{ entry.note || entry.account }}</text></view>
        <view class="entry-amount"><text class="entry-number number" :class="{ 'is-income': entry.type === 'income' }">{{ entry.type === 'income' ? '+' : '−' }}{{ money(entry.amount) }}</text><text class="entry-account">{{ entry.time ? `${entry.time} · ` : '' }}{{ entry.account }}</text></view>
      </button>
    </view>
  </view>
</template>

<style scoped lang="scss">
.day-group { margin-bottom: 25rpx; }
.day-heading { display: flex; justify-content: space-between; align-items: center; color: #829086; font-size: 23rpx; padding: 0 4rpx 15rpx; }
.day-totals { display: flex; gap: 16rpx; font-size: 21rpx; }
.entries-panel { background: #fff; border-radius: 26rpx; padding: 2rpx 24rpx; }
.entry { display: flex; align-items: center; width: 100%; text-align: left; padding: 25rpx 0; border-bottom: 1rpx solid #f0f3f1; }
.entry:last-child { border-bottom: 0; }
.category-icon { width: 78rpx; height: 78rpx; border-radius: 25rpx; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.entry-info { display: flex; flex-direction: column; flex: 1; min-width: 0; padding: 0 18rpx; }
.entry-category { font-size: 28rpx; font-weight: 500; }
.entry-note { font-size: 22rpx; color: #96a098; margin-top: 6rpx; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.entry-amount { display: flex; flex-direction: column; align-items: flex-end; flex-shrink: 0; }
.entry-number { font-size: 32rpx; font-weight: 500; }
.is-income { color: #b47a46; }
.entry-account { font-size: 20rpx; color: #a2aaa4; margin-top: 5rpx; }
</style>
