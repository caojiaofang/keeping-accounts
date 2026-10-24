<script setup lang="ts">
import { useLedgerStore } from '@/store/ledger'
const store = useLedgerStore()
</script>

<template>
  <view v-if="store.accountId" class="cloud-status" :class="{ failed: store.syncError }">
    <text>{{ store.writing ? '正在保存到云端…' : store.syncing ? '正在读取云端账本…' : store.syncError || (store.cloudReady ? '账本已连接云端' : '云端账本尚未就绪') }}</text>
    <button :disabled="store.syncing || store.writing" @tap="store.refreshCloud()">{{ store.syncError ? '重新读取' : '刷新' }}</button>
  </view>
</template>

<style scoped lang="scss">
.cloud-status { display: flex; align-items: center; gap: 18rpx; margin: 12rpx 0 22rpx; padding: 17rpx 22rpx; border-radius: 16rpx; background: #e8f0e8; color: #567461; font-size: 23rpx; }
.cloud-status text { flex: 1; }
.cloud-status button { flex-shrink: 0; color: #285e4e; font-size: 23rpx; }
.cloud-status.failed { background: #f8eee5; color: #946449; }
</style>
