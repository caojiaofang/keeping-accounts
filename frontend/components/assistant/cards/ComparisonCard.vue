<script setup lang="ts">
import type { FactRefV2, ResultCardV2 } from '@/common/types/chat-v2'
import { cardFacts } from '@/common/services/chat-card'
import FactList from './FactList.vue'
defineProps<{ card: ResultCardV2; facts: FactRefV2[] }>()
</script>
<template>
  <view>
    <FactList :facts="cardFacts(card.factIds, facts)" prominent />
    <text class="note">差额表示本期减上期；贡献占比可能为负或超过 100%，不代表生活原因。</text>
    <view v-for="(row, index) in card.rows" :key="row.groupKey || index" class="comparison"><text class="title">{{ row.label }}{{ row.isOther ? '（其余分组合计）' : '' }}</text><text v-if="row.detail" class="note">{{ row.detail }}</text><FactList :facts="cardFacts(row.factIds, facts)" /></view>
    <text v-if="!card.rows.length" class="note">本卡没有返回分组明细，请以上方比较事实为准。</text>
  </view>
</template>
<style scoped>
.comparison { padding: 18rpx 0 6rpx; border-top: 1rpx solid #dfe8e1; }
.title { display: block; font-size: 25rpx; font-weight: 500; overflow-wrap: anywhere; }
.note { display: block; font-size: 21rpx; line-height: 1.7; color: #687e70; margin: 8rpx 0 12rpx; overflow-wrap: anywhere; white-space: pre-wrap; }
</style>
