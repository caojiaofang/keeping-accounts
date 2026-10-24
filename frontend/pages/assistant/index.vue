<script setup lang="ts">
import { ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import TopBar from '@/components/TopBar.vue'
import AppIcon from '@/components/AppIcon.vue'
import QuickRecord from '@/components/assistant/QuickRecord.vue'
import ExpenseChat from '@/components/assistant/ExpenseChat.vue'
import { useLedgerStore } from '@/store/ledger'
import { localDate } from '@/common/utils/format'

const store = useLedgerStore()
store.hydrate()
const mode = ref('record')
const month = ref(localDate().slice(0, 7))
onLoad(options => {
  mode.value = options?.mode === 'chat' ? 'chat' : 'record'
  if (options?.month && /^\d{4}-(0[1-9]|1[0-2])$/.test(options.month)) month.value = options.month
})
</script>

<template>
  <view class="assistant-page">
    <view class="assistant-header">
      <TopBar title="墨记助手" back />
      <view class="mode-switch"><button class="mode-tab" :class="{ active: mode === 'record' }" @tap="mode = 'record'"><AppIcon name="edit" :size="29" />一句话记账</button><button class="mode-tab" :class="{ active: mode === 'chat' }" @tap="mode = 'chat'"><AppIcon name="chart" :size="29" />聊聊花费</button></view>
      <view class="assistant-context"><view class="book-context"><AppIcon name="book" :size="24" /><text>{{ store.activeBook.name }}</text></view><text class="demo-label">页面演示 · 暂未接入大模型</text></view>
    </view>
    <view class="assistant-content" :style="{ display: mode === 'record' ? 'flex' : 'none' }"><QuickRecord /></view>
    <view class="assistant-content" :style="{ display: mode === 'chat' ? 'flex' : 'none' }"><ExpenseChat :month="month" /></view>
  </view>
</template>

<style scoped lang="scss">
.assistant-page { height: 100vh; height: 100dvh; overflow: hidden; display: flex; flex-direction: column; background: #f6f8f7; }
.assistant-header { padding: 0 36rpx; flex-shrink: 0; }
.assistant-content { display: flex; flex-direction: column; flex: 1; min-height: 0; overflow: hidden; }
.mode-switch { display: flex; padding: 7rpx; background: #e7eee9; border-radius: 23rpx; gap: 8rpx; }
.mode-tab { flex: 1; display: flex; align-items: center; justify-content: center; gap: 12rpx; border-radius: 18rpx; padding: 17rpx 4rpx; color: #7a9083; font-size: 25rpx; }
.mode-tab.active { color: #285e4e; background: #fff; font-weight: 600; }
.assistant-context { display: flex; align-items: center; justify-content: space-between; padding: 20rpx 0; gap: 12rpx; }
.book-context { display: flex; gap: 8rpx; align-items: center; font-size: 21rpx; min-width: 0; }
.book-context text { max-width: 230rpx; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.demo-label { font-size: 19rpx; color: #8b978f; flex-shrink: 0; }
</style>
