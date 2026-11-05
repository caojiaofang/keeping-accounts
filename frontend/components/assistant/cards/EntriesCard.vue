<script setup lang="ts">
import type { Book } from '@/common/types'
import type { FactRefV2, ResultCardV2, ResultRowV2 } from '@/common/types/chat-v2'
import { cardFacts } from '@/common/services/chat-card'
import { categories } from '@/common/data/categories'
import FactList from './FactList.vue'
defineProps<{ card: ResultCardV2; facts: FactRefV2[]; book?: Book; interactive: boolean }>()
defineEmits<{ open: [entryId: string] }>()
function author(row: ResultRowV2, book?: Book): string {
  return book?.members?.find(member => member.userId === row.creatorId)?.name || '历史记账人'
}
</script>
<template>
  <view>
    <text class="explanation">以下合计与笔数覆盖全部匹配账单</text><FactList :facts="cardFacts(card.factIds, facts)" prominent />
    <text class="page-count">本卡展示 {{ card.rows.length }} 笔；不是仅对下列账单求和</text>
    <view v-for="row in card.rows" :key="row.entryId!" class="entry">
      <text class="date">{{ row.date }} {{ row.time || '' }} · {{ row.entryType === 'income' ? '收入' : '支出' }}</text>
      <text class="title" selectable>{{ row.label }}</text><text v-if="row.detail && row.detail !== row.label" class="detail" selectable>{{ row.detail }}</text>
      <text class="detail">{{ categories.find(category => category.id === row.categoryId)?.name || '历史分类' }} · {{ row.account }}<template v-if="book?.kind === 'shared'"> · {{ author(row, book) }}</template></text>
      <text v-if="row.matchedKeywords?.length" class="match">备注匹配：{{ row.matchedKeywords.join('、') }}</text>
      <FactList :facts="cardFacts(row.factIds, facts)" />
      <button class="open-entry" :disabled="!interactive" @tap="$emit('open', row.entryId!)">{{ interactive ? '查看当前账单' : '回答保存后可查看账单' }}</button>
    </view>
    <text v-if="!card.rows.length" class="detail">本卡没有可展示的账单，请结合上方统计与回答说明查看。</text>
  </view>
</template>
<style scoped>
.explanation, .page-count, .date, .detail, .match { display: block; font-size: 21rpx; line-height: 1.7; color: #687e70; overflow-wrap: anywhere; white-space: pre-wrap; }
.explanation { margin-top: 18rpx; }
.page-count { padding: 8rpx 0 16rpx; }
.entry { border-top: 1rpx solid #dfe8e1; padding: 18rpx 0; }
.title { display: block; font-size: 26rpx; line-height: 1.7; overflow-wrap: anywhere; white-space: pre-wrap; margin: 6rpx 0; }
.match { color: #285e4e; margin-top: 8rpx; }
.open-entry { display: inline-flex; padding: 12rpx 0; color: #285e4e; font-size: 24rpx; text-align: left; }
.open-entry[disabled] { background: transparent; color: #687e70; font-size: 21rpx; }
</style>
