<script setup lang="ts">
import { computed } from 'vue'
import type { FactRefV2, ResultCardV2, ResultRowV2 } from '@/common/types/chat-v2'
import { cardFacts, factBar } from '@/common/services/chat-card'
import FactList from './FactList.vue'
const props = defineProps<{ card: ResultCardV2; facts: FactRefV2[] }>()
const amounts = (row: ResultRowV2) => cardFacts(row.factIds, props.facts).filter(fact => fact.unit === 'cents' && fact.metric !== 'difference' && fact.metric !== 'previous_total')
const maximum = computed(() => Math.max(0, ...props.card.rows.flatMap(row => amounts(row).map(fact => Math.abs(Number(fact.value))))))
</script>
<template>
  <view>
    <FactList :facts="cardFacts(card.factIds, facts)" prominent />
    <text class="note">仅展示返回的记录日期，不把缺失日期补成零消费。</text>
    <view v-for="(row, index) in card.rows" :key="row.date || index" class="period"><text class="date">{{ row.date || row.label }}</text><text v-if="row.date && row.label !== row.date" class="note">{{ row.label }}</text><view v-for="fact in amounts(row)" :key="fact.id" class="bar-track"><view class="bar" :style="{ width: factBar(Number(fact.value), maximum) }" /></view><FactList :facts="cardFacts(row.factIds, facts)" /><text v-if="row.detail" class="note">{{ row.detail }}</text></view>
    <text v-if="!card.rows.length" class="note">本卡没有可展示的日期记录。</text>
  </view>
</template>
<style scoped>
.period { border-top: 1rpx solid #dfe8e1; padding: 18rpx 0 8rpx; }
.date { display: block; font-size: 24rpx; }
.note { display: block; font-size: 21rpx; line-height: 1.7; color: #687e70; margin-bottom: 12rpx; overflow-wrap: anywhere; }
.bar-track { background: #e6ede8; height: 8rpx; margin-top: 12rpx; }
.bar { background: #709a80; height: 100%; }
</style>
