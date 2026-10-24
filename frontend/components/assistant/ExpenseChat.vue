<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import AppIcon from '@/components/AppIcon.vue'
import { analysisQuestions, buildDemoAnalysis } from '@/common/mock/assistant'
import type { AnalysisResult, AnalysisTopic } from '@/common/mock/assistant'
import { categories } from '@/common/data/categories'
import { useLedgerStore } from '@/store/ledger'
import { money, monthLabel } from '@/common/utils/format'

defineOptions({ options: { virtualHost: true } })
const props = defineProps<{ month: string }>()
const store = useLedgerStore()
interface Message { id: number; role: 'user' | 'assistant'; text: string; result?: AnalysisResult; bookName?: string }
const messages = ref<Message[]>([])
const input = ref('')
const anchor = ref('')
const focusCategory = ref<string>()
const sequence = ref(0)
const lastRankingCategory = ref('food')
const suggestions = computed(() => messages.value.length ? [
  { text: `展开${store.categoryOf(lastRankingCategory.value).name}明细`, topic: 'details' as const, categoryId: lastRankingCategory.value },
  { text: focusCategory.value ? '这一类和上月相比呢？' : '和上个月相比呢？', topic: 'compare' as const, categoryId: focusCategory.value },
  { text: '列出超过200元的消费', topic: 'large' as const, categoryId: undefined },
  { text: '有哪些支出可以减少？', topic: 'advice' as const, categoryId: undefined },
] : analysisQuestions.map(item => ({ ...item, categoryId: item.topic === 'details' ? 'food' : undefined })))

async function scrollToBottom() {
  anchor.value = ''
  await nextTick()
  anchor.value = 'conversation-end'
}
function ask(text: string, topic?: AnalysisTopic, categoryId?: string) {
  if (!text.trim()) return
  const question = text.trim()
  const suggested = suggestions.value.find(item => item.text === question)
  const known = suggested || analysisQuestions.find(item => item.text === question)
  const aliases: Record<string, AnalysisTopic> = { '我这个月的花费主要集中在什么地方': 'overview', '我这个月的花费主要集中在什么地方？': 'overview', '和上个月相比呢': 'compare' }
  const resolved = topic || known?.topic || aliases[question]
  messages.value.push({ id: ++sequence.value, role: 'user', text: question })
  input.value = ''
  if (!resolved) {
    messages.value.push({ id: ++sequence.value, role: 'assistant', text: '当前为静态对话演示，暂时不能理解任意问题。可以点下方的问题，体验分类分析、消费明细和连续追问；接入大模型后将支持自由对话。' })
    scrollToBottom()
    return
  }
  const category = resolved === 'details' ? categoryId || suggested?.categoryId || 'food' : resolved === 'compare' ? categoryId || focusCategory.value : undefined
  focusCategory.value = category
  const result = buildDemoAnalysis(store.bookEntries, categories, props.month, resolved, category)
  if (resolved === 'overview' || resolved === 'advice') {
    lastRankingCategory.value = categories.find(item => item.name === result.rows[0]?.label && item.type === 'expense')?.id || 'food'
  }
  if (resolved === 'details' && category) lastRankingCategory.value = category
  messages.value.push({ id: ++sequence.value, role: 'assistant', text: result.text, result, bookName: store.activeBook.name })
  scrollToBottom()
}
function reset() {
  if (!messages.value.length) return
  uni.showModal({ title: '开始新的对话？', content: '将清空本页对话，已经保存的账单不会受影响。', confirmText: '新对话', confirmColor: '#285e4e', success: result => {
    if (result.confirm) { messages.value = []; focusCategory.value = undefined; lastRankingCategory.value = 'food'; input.value = '' }
  } })
}
</script>

<template>
  <view class="chat-panel">
    <view class="chat-toolbar"><text>{{ monthLabel(month) }} · 支出分析</text><button class="new-conversation" :disabled="!messages.length" @tap="reset"><AppIcon name="plus" :size="23" />新对话</button></view>
    <scroll-view scroll-y class="conversation-scroll" :scroll-into-view="anchor" scroll-with-animation>
      <view class="conversation-body">
        <view v-if="!messages.length" class="chat-welcome"><view class="assistant-mark"><AppIcon name="leaf" :size="53" light /></view><text class="welcome-title">钱花在哪，一起聊清楚。</text><text class="welcome-copy">从一笔早餐，到一个月的生活，
让每笔记录都有回响。</text><view class="welcome-scope"><AppIcon name="book" :size="27" /><text>只分析「{{ store.activeBook.name }}」</text></view><view class="suggested-title">可以从这些问题开始</view><button v-for="question in suggestions" :key="question.text" class="welcome-question" @tap="ask(question.text, question.topic, question.categoryId)"><text>{{ question.text }}</text><AppIcon name="arrow" :size="26" /></button><text class="welcome-note">演示回答来自本地账单；对话仅保留在本次页面中。</text></view>
        <view v-for="message in messages" :key="message.id" class="message" :class="message.role">
          <view v-if="message.role === 'assistant'" class="message-author"><view class="small-mark"><AppIcon name="leaf" :size="24" light /></view><text>墨记助手</text><text class="local-answer">本地演示</text></view>
          <view class="message-bubble"><text class="message-text" selectable>{{ message.text }}</text>
            <view v-if="message.result" class="answer-card">
              <text class="answer-scope">{{ message.bookName }} · {{ message.result.scope }}</text><text class="answer-title">{{ message.result.title }}</text>
              <view class="answer-total"><text class="answer-currency">¥</text><text class="answer-value number">{{ money(message.result.total) }}</text><text class="answer-count">{{ message.result.count }} 笔支出</text></view>
              <view v-for="(row, index) in message.result.rows" :key="index" class="answer-row"><view class="answer-icon" :style="{ background: row.color }"><AppIcon :name="row.icon" :size="29" /></view><view class="answer-row-main"><view class="between"><text class="answer-label">{{ row.label }}</text><text class="answer-amount number">{{ money(row.amount) }}</text></view><view v-if="row.percent !== undefined" class="answer-distribution"><view class="answer-track"><view :style="{ width: `${row.percent}%` }" /></view><text class="number">{{ row.percent }}%</text></view><text v-if="row.detail" class="answer-detail">{{ row.detail }}</text></view></view>
              <text v-if="message.result.footnote" class="answer-footnote">{{ message.result.footnote }}</text>
            </view>
          </view>
        </view>
        <view v-if="messages.length" class="followup"><text class="followup-caption">接着问问</text><view class="followup-list"><button v-for="question in suggestions" :key="question.text" class="followup-question" @tap="ask(question.text, question.topic, question.categoryId)">{{ question.text }}<AppIcon name="plus" :size="22" /></button></view></view>
        <view id="conversation-end" class="conversation-end" />
      </view>
    </scroll-view>
    <view class="chat-composer"><view class="compose-box"><textarea v-model="input" class="chat-input" placeholder="问问这月的花费，或点选示例问题" :maxlength="200" auto-height :adjust-position="true" :cursor-spacing="24" confirm-type="send" @confirm="ask(input)" /><button class="send-message" :disabled="!input.trim()" aria-label="发送消息" @tap="ask(input)"><AppIcon name="arrow" :size="31" light /></button></view><text class="composer-note">基于已记录的账单分析，实际收支以你的核对为准</text></view>
  </view>
</template>

<style scoped lang="scss">
.chat-panel { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.chat-toolbar { display: flex; align-items: center; justify-content: space-between; margin: 0 36rpx; padding: 8rpx 0 16rpx; border-bottom: 1rpx solid #e4ece6; font-size: 22rpx; color: #8b9d90; }
.new-conversation { display: flex; align-items: center; gap: 5rpx; font-size: 22rpx; color: #668671; padding: 7rpx 0 7rpx 18rpx; }
.new-conversation[disabled] { background: transparent; color: #a6b2a9; }
.conversation-scroll { flex: 1; height: 0; min-height: 0; }
.conversation-body { padding: 25rpx 36rpx 0; }
.chat-welcome { padding: 14rpx 0 28rpx; }
.assistant-mark { display: flex; align-items: center; justify-content: center; width: 91rpx; height: 91rpx; background: #285e4e; border-radius: 30rpx 30rpx 30rpx 9rpx; margin-bottom: 28rpx; }
.welcome-title { display: block; font-size: 38rpx; font-weight: 600; letter-spacing: 1rpx; }
.welcome-copy { display: block; font-size: 25rpx; line-height: 1.9; color: #8d9e91; white-space: pre-line; margin-top: 14rpx; }
.welcome-scope { display: flex; align-items: center; gap: 9rpx; color: #6f8c78; font-size: 22rpx; margin-top: 24rpx; }
.suggested-title { font-size: 22rpx; color: #9baa9e; margin: 34rpx 0 17rpx; }
.welcome-question { display: flex; align-items: center; justify-content: space-between; gap: 18rpx; width: 100%; background: #fff; border: 1rpx solid #e5ede7; padding: 22rpx; border-radius: 18rpx; font-size: 25rpx; text-align: left; margin-bottom: 13rpx; }
.welcome-note { display: block; font-size: 20rpx; color: #a4afa7; margin-top: 24rpx; }
.message { margin-bottom: 30rpx; }
.message.user { display: flex; justify-content: flex-end; }
.message-bubble { background: #fff; padding: 24rpx; border-radius: 4rpx 24rpx 24rpx 24rpx; }
.user .message-bubble { max-width: 89%; background: #dfece3; border-radius: 24rpx 4rpx 24rpx 24rpx; padding: 19rpx 25rpx; }
.message-text { font-size: 27rpx; line-height: 1.85; white-space: pre-wrap; overflow-wrap: anywhere; }
.message-author { display: flex; align-items: center; gap: 10rpx; margin-bottom: 13rpx; color: #6c8874; font-size: 21rpx; }
.small-mark { display: flex; align-items: center; justify-content: center; width: 38rpx; height: 38rpx; border-radius: 12rpx 12rpx 12rpx 3rpx; background: #376b51; }
.local-answer { margin-left: auto; font-size: 19rpx; color: #a5afa7; }
.answer-card { margin-top: 23rpx; padding: 22rpx; border: 1rpx solid #e3ece4; border-radius: 18rpx; background: #fbfcfa; }
.answer-scope { display: block; color: #94a396; font-size: 19rpx; }
.answer-title { display: block; font-size: 26rpx; font-weight: 600; margin-top: 10rpx; }
.answer-total { display: flex; align-items: baseline; margin: 12rpx 0 16rpx; gap: 9rpx; flex-wrap: wrap; }
.answer-currency { font-size: 25rpx; }
.answer-value { font-size: 45rpx; }
.answer-count { margin-left: auto; color: #96a498; font-size: 20rpx; }
.answer-row { display: flex; align-items: center; gap: 13rpx; padding: 15rpx 0; border-top: 1rpx solid #e9efe8; }
.answer-icon { width: 52rpx; height: 52rpx; border-radius: 17rpx; flex-shrink: 0; display: flex; align-items: center; justify-content: center; }
.answer-row-main { flex: 1; min-width: 0; }
.answer-row-main .between { align-items: flex-start; gap: 10rpx; }
.answer-label { font-size: 23rpx; overflow-wrap: anywhere; flex: 1; }
.answer-amount { font-size: 25rpx; flex-shrink: 0; }
.answer-distribution { display: flex; align-items: center; gap: 13rpx; margin-top: 7rpx; font-size: 18rpx; color: #8e9f91; }
.answer-track { flex: 1; height: 6rpx; border-radius: 6rpx; background: #e7eee7; overflow: hidden; }
.answer-track > view { height: 100%; background: #88aa90; border-radius: 6rpx; }
.answer-detail { display: block; margin-top: 5rpx; font-size: 18rpx; color: #9baa9c; }
.answer-footnote { display: block; padding-top: 16rpx; font-size: 19rpx; line-height: 1.75; color: #9eaa9e; border-top: 1rpx solid #e9efe8; }
.followup-caption { display: block; font-size: 20rpx; color: #97a69a; margin-bottom: 12rpx; }
.followup-list { display: flex; flex-wrap: wrap; gap: 13rpx; }
.followup-question { display: flex; align-items: center; gap: 10rpx; font-size: 22rpx; color: #688471; background: #eaf1e9; border-radius: 14rpx; padding: 14rpx 18rpx; }
.conversation-end { height: 35rpx; }
.chat-composer { flex-shrink: 0; padding: 18rpx 30rpx calc(13rpx + env(safe-area-inset-bottom)); background: #f6f8f7; border-top: 1rpx solid #e5ece6; }
.compose-box { padding: 15rpx 15rpx 15rpx 22rpx; display: flex; align-items: flex-end; gap: 10rpx; background: #fff; border: 1rpx solid #dfe8e1; border-radius: 24rpx; }
.chat-input { flex: 1; min-width: 0; min-height: 57rpx; max-height: 160rpx; font-size: 25rpx; line-height: 1.6; padding: 8rpx 0; }
.send-message { width: 62rpx; height: 62rpx; display: flex; align-items: center; justify-content: center; background: #285e4e; border-radius: 18rpx; flex-shrink: 0; }
.send-message[disabled] { background: #bdcfc2; }
.composer-note { display: block; font-size: 18rpx; text-align: center; margin-top: 12rpx; color: #9eaaa1; }
</style>
