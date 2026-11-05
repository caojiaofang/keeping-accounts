<script setup lang="ts">
import type { FactRefV2, ResultCardV2 } from '@/common/types/chat-v2'
import { cardFacts } from '@/common/services/chat-card'
import FactList from './FactList.vue'
defineProps<{ card: ResultCardV2; facts: FactRefV2[] }>()
</script>
<template>
  <view>
    <FactList :facts="cardFacts(card.factIds, facts)" prominent />
    <view v-for="(row, index) in card.rows" :key="row.groupKey || index" class="group"><text class="group-title">{{ row.label }}</text><FactList :facts="cardFacts(row.factIds, facts)" /><text v-if="row.detail" class="detail">{{ row.detail }}</text></view>
  </view>
</template>
<style scoped>
.group { padding: 18rpx 0 4rpx; border-top: 1rpx solid #dfe8e1; }
.group-title { font-size: 25rpx; font-weight: 500; overflow-wrap: anywhere; }
.detail { display: block; font-size: 21rpx; color: #687e70; white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
