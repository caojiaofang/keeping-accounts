<script setup lang="ts">
import type { FactRefV2 } from '@/common/types/chat-v2'
import { formatFactV2 } from '@/common/api/chat-v2-codec'
defineProps<{ facts: FactRefV2[]; prominent?: boolean }>()
</script>

<template>
  <view class="facts" :class="{ prominent }">
    <view v-for="fact in facts" :key="fact.id" class="fact"><text class="label">{{ fact.label }}</text><text class="value number" :class="{ decrease: fact.metric === 'difference' && Number(fact.value) < 0 }" selectable>{{ formatFactV2(fact) }}</text></view>
  </view>
</template>

<style scoped>
.facts { display: flex; flex-wrap: wrap; gap: 12rpx 24rpx; margin: 12rpx 0; }
.fact { min-width: 0; display: flex; flex-direction: column; gap: 5rpx; }
.label { font-size: 21rpx; color: #687e70; overflow-wrap: anywhere; }
.value { font-size: 25rpx; color: #294b3b; overflow-wrap: anywhere; }
.prominent .value { font-size: 32rpx; font-weight: 600; }
.decrease { color: #586f85; }
</style>
