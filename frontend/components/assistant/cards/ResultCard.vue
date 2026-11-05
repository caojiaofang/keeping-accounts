<script setup lang="ts">
import { computed } from 'vue'
import type { Book } from '@/common/types'
import type { FactRefV2, ResultCardV2 } from '@/common/types/chat-v2'
import { filterDescriptions, statisticTime } from '@/common/services/chat-card'
import SummaryCard from './SummaryCard.vue'
import EntriesCard from './EntriesCard.vue'
import TrendCard from './TrendCard.vue'
import ComparisonCard from './ComparisonCard.vue'
const props = defineProps<{ card: ResultCardV2; facts: FactRefV2[]; book?: Book; interactive: boolean; partial?: boolean }>()
defineEmits<{ open: [entryId: string] }>()
const filters = computed(() => filterDescriptions(props.card.filters, props.book))
</script>
<template>
  <view class="result-card">
    <text class="scope">{{ book?.name }} · {{ card.filters.dateRange.start }} 至 {{ card.filters.dateRange.end }}</text>
    <text v-if="card.previousRange" class="scope previous">上期 {{ card.previousRange.start }} 至 {{ card.previousRange.end }}</text>
    <text class="title">{{ card.title }}</text><text v-if="partial" class="partial">部分结果 · 请结合回答中的未完成说明查看</text>
    <SummaryCard v-if="card.type === 'summary'" :card="card" :facts="facts" />
    <EntriesCard v-else-if="card.type === 'entries'" :card="card" :facts="facts" :book="book" :interactive="interactive" @open="$emit('open', $event)" />
    <TrendCard v-else-if="card.type === 'trend'" :card="card" :facts="facts" />
    <ComparisonCard v-else :card="card" :facts="facts" />
    <view class="criteria"><text class="criteria-title">本卡筛选条件</text><text v-for="(filter, index) in filters" :key="index" class="criterion">{{ filter }}</text></view>
    <text v-if="card.footnote" class="card-footnote">{{ card.footnote }}</text>
    <text class="updated">统计于 {{ statisticTime(card.generatedAt) }} · 历史快照</text>
  </view>
</template>
<style scoped>
.result-card { margin-top: 24rpx; padding: 22rpx; border-left: 5rpx solid #709a80; background: #f5f8f5; border-radius: 0 16rpx 16rpx 0; min-width: 0; }
.scope, .card-footnote, .updated, .criterion, .partial { display: block; font-size: 21rpx; line-height: 1.7; color: #687e70; overflow-wrap: anywhere; white-space: pre-wrap; }
.previous { margin-top: 4rpx; }
.title { display: block; font-size: 28rpx; font-weight: 600; margin: 12rpx 0; overflow-wrap: anywhere; }
.partial { color: #946449; }
.criteria { border-top: 1rpx solid #dfe8e1; padding-top: 16rpx; margin-top: 16rpx; }
.criteria-title { display: block; font-size: 21rpx; color: #294b3b; margin-bottom: 6rpx; }
.card-footnote { margin-top: 16rpx; }
.updated { margin-top: 12rpx; font-size: 19rpx; }
</style>
