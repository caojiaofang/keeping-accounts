<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import AppIcon from '@/components/AppIcon.vue'
import { analysisQuestions, buildDemoAnalysis } from '@/common/mock/assistant'
import type { AnalysisResult, AnalysisTopic } from '@/common/mock/assistant'
import { categories } from '@/common/data/categories'
import { useLedgerStore } from '@/store/ledger'
import { useChatStore } from '@/store/chat'
import { money, monthLabel } from '@/common/utils/format'

defineOptions({ options: { virtualHost: true } })
const props = defineProps<{ month: string; active: boolean }>()
const store = useLedgerStore()
interface Message { id: number | string; role: 'user' | 'assistant'; text: string; result?: AnalysisResult; bookName?: string }
const demoMessages = ref<Message[]>([])
const chat = useChatStore()
const historyOpen = ref(false)
const busy = computed(() => !!store.accountId && chat.busy)
const blocked = computed(() => busy.value || (!!store.accountId && !!chat.pending))
const messages = computed<Message[]>(() => store.accountId ? [
  ...chat.turns.flatMap(turn => [
    { id: `${turn.id}-user`, role: 'user' as const, text: turn.text },
    { id: `${turn.id}-assistant`, role: 'assistant' as const, text: turn.answer, result: turn.result, bookName: store.activeBook.name },
  ]),
  ...(chat.pending ? [{ id: chat.pending.id, role: 'user' as const, text: chat.pending.text }] : []),
] : demoMessages.value)
let loadedScope = ''
const input = ref('')
const anchor = ref('')
const focusCategory = ref<string>()
const sequence = ref(0)
const lastRankingCategory = ref('food')
const rankingCategory = computed(() => {
  if (!store.accountId) return lastRankingCategory.value
  if (chat.context?.categoryId) return chat.context.categoryId
  const latest = chat.turns[chat.turns.length - 1]
  return categories.find(category => category.type === chat.context?.entryType && category.name === latest?.result?.rows[0]?.label)?.id || (chat.context?.entryType === 'income' ? 'salary' : 'food')
})
watch([() => store.accountId, () => store.activeBookId, () => store.cloudReady, () => props.month, () => props.active], async () => {
  if (!store.accountId) { loadedScope = ''; return }
  if (!props.active || !store.cloudReady) return
  const scope = `${store.accountId}:${store.activeBookId}:${props.month}`
  if (loadedScope === scope) return
  loadedScope = scope
  input.value = ''
  historyOpen.value = false
  await chat.load(props.month)
  void scrollToBottom()
}, { immediate: true })
const suggestions = computed(() => messages.value.length ? [
  { text: `展开${store.categoryOf(rankingCategory.value).name}明细`, topic: 'details' as const, categoryId: rankingCategory.value },
  { text: (store.accountId ? chat.context?.categoryId : focusCategory.value) ? '这一类和上月相比呢？' : '和上个月相比呢？', topic: 'compare' as const, categoryId: focusCategory.value },
  { text: '列出超过200元的消费', topic: 'large' as const, categoryId: undefined },
  { text: '有哪些支出可以减少？', topic: 'advice' as const, categoryId: undefined },
] : analysisQuestions.map(item => ({ ...item, categoryId: item.topic === 'details' ? 'food' : undefined })))

async function scrollToBottom() {
  anchor.value = ''
  await nextTick()
  anchor.value = 'conversation-end'
}
async function ask(text: string, topic?: AnalysisTopic, categoryId?: string) {
  if (store.accountId) {
    if (busy.value || !text.trim()) return
    input.value = ''
    const task = chat.send(text)
    void scrollToBottom()
    await task
    void scrollToBottom()
    return
  }
  if (!text.trim()) return
  const question = text.trim()
  const suggested = suggestions.value.find(item => item.text === question)
  const known = suggested || analysisQuestions.find(item => item.text === question)
  const aliases: Record<string, AnalysisTopic> = { '我这个月的花费主要集中在什么地方': 'overview', '我这个月的花费主要集中在什么地方？': 'overview', '和上个月相比呢': 'compare' }
  const resolved = topic || known?.topic || aliases[question]
  demoMessages.value.push({ id: ++sequence.value, role: 'user', text: question })
  input.value = ''
  if (!resolved) {
    demoMessages.value.push({ id: ++sequence.value, role: 'assistant', text: '当前为静态对话演示，暂时不能理解任意问题。可以点下方的问题，体验分类分析、消费明细和连续追问；接入大模型后将支持自由对话。' })
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
  demoMessages.value.push({ id: ++sequence.value, role: 'assistant', text: result.text, result, bookName: store.activeBook.name })
  scrollToBottom()
}
function reset() {
  if (busy.value) return
  uni.showModal({ title: '开始新的对话？', content: store.accountId ? '将开始新的对话，原对话仍可从历史记录继续，账单不受影响。' : '将清空本页对话，已经保存的账单不会受影响。', confirmText: '新对话', confirmColor: '#285e4e', success: result => {
    if (result.confirm) { if (store.accountId) chat.startNew(); else demoMessages.value = []; focusCategory.value = undefined; lastRankingCategory.value = 'food'; input.value = '' }
  } })
}

async function reload() {
  if (busy.value) return
  if (chat.conversationId) await chat.read(chat.conversationId)
  else await chat.load(props.month)
  void scrollToBottom()
}
async function openHistory() { if (busy.value) return; historyOpen.value = true; await chat.refreshHistory() }
async function chooseHistory(id: string) { if (await chat.read(id)) { historyOpen.value = false; void scrollToBottom() } }
</script>

<template>
  <view class="chat-panel">
    <view class="chat-toolbar"><text>{{ monthLabel(store.accountId ? chat.context?.month || month : month) }} · 收支分析</text><button v-if="store.accountId" class="new-conversation" :disabled="busy" @tap="openHistory">历史</button><button class="new-conversation" :disabled="busy" @tap="reset"><AppIcon name="plus" :size="23" />新对话</button></view>
    <scroll-view scroll-y class="conversation-scroll" :scroll-into-view="anchor" scroll-with-animation>
      <view class="conversation-body"><button v-if="store.accountId && chat.hasMore" class="older-turns" :disabled="busy" @tap="chat.loadEarlier()">查看更早的对话</button>
        <view v-if="!messages.length && !busy" class="chat-welcome"><view class="assistant-mark"><AppIcon name="leaf" :size="53" light /></view><text class="welcome-title">钱花在哪，一起聊清楚。</text><text class="welcome-copy">从一笔早餐，到一个月的生活，
让每笔记录都有回响。</text><view class="welcome-scope"><AppIcon name="book" :size="27" /><text>只分析「{{ store.activeBook.name }}」</text></view><view class="suggested-title">可以从这些问题开始</view><button v-for="question in suggestions" :key="question.text" class="welcome-question" :disabled="blocked" @tap="ask(question.text, question.topic, question.categoryId)"><text>{{ question.text }}</text><AppIcon name="arrow" :size="26" /></button><text class="welcome-note">{{ store.accountId ? '按需读取当前账本进行分析，对话保存在云端；模型只会收到本轮所需的统计或明细。' : '体验模式根据当前账本演示分析；微信登录后支持自由提问和历史对话。' }}</text></view>
        <view v-for="message in messages" :key="message.id" class="message" :class="message.role">
          <view v-if="message.role === 'assistant'" class="message-author"><view class="small-mark"><AppIcon name="leaf" :size="24" light /></view><text>墨记助手</text><text class="local-answer">{{ store.accountId ? '智能分析' : '本地演示' }}</text></view>
          <view class="message-bubble"><text class="message-text" selectable>{{ message.text }}</text>
            <view v-if="message.result" class="answer-card">
              <text class="answer-scope">{{ message.bookName }} · {{ message.result.scope }}</text><text class="answer-title">{{ message.result.title }}</text>
              <view class="answer-total"><text class="answer-currency">¥</text><text class="answer-value number">{{ money(message.result.total) }}</text><text class="answer-count">{{ message.result.count }} 笔{{ message.result.entryType === 'income' ? '收入' : '支出' }}</text></view>
              <view v-for="(row, index) in message.result.rows" :key="index" class="answer-row"><view class="answer-icon" :style="{ background: row.color }"><AppIcon :name="row.icon" :size="29" /></view><view class="answer-row-main"><view class="between"><text class="answer-label">{{ row.label }}</text><text class="answer-amount number">{{ money(row.amount) }}</text></view><view v-if="row.percent !== undefined" class="answer-distribution"><view class="answer-track"><view :style="{ width: `${row.percent}%` }" /></view><text class="number">{{ row.percent }}%</text></view><text v-if="row.detail" class="answer-detail">{{ row.detail }}</text></view></view>
              <text v-if="message.result.footnote" class="answer-footnote">{{ message.result.footnote }}</text>
            </view>
          </view>
        </view>
        <view v-if="store.accountId && busy" class="chat-status">{{ chat.sending ? '正在查询账单并分析…' : '正在读取对话…' }}</view>
        <view v-if="store.accountId && chat.error" class="chat-error"><text>{{ chat.error }}</text><view><button v-if="chat.pending" :disabled="busy" @tap="ask(chat.pending.text)">重试这条消息</button><button :disabled="busy" @tap="reload">重新读取</button></view></view>
        <view v-if="messages.length" class="followup"><text class="followup-caption">接着问问</text><view class="followup-list"><button v-for="question in suggestions" :key="question.text" class="followup-question" :disabled="blocked" @tap="ask(question.text, question.topic, question.categoryId)">{{ question.text }}<AppIcon name="plus" :size="22" /></button></view></view>
        <view id="conversation-end" class="conversation-end" />
      </view>
    </scroll-view>
    <view class="chat-composer"><view class="compose-box"><textarea v-model="input" class="chat-input" :placeholder="store.accountId ? '问问这月的收支，也可以接着追问' : '问问这月的花费，或点选示例问题'" :disabled="blocked" :maxlength="200" auto-height :adjust-position="true" :cursor-spacing="24" confirm-type="send" @confirm="ask(input)" /><button class="send-message" :disabled="blocked || !input.trim()" aria-label="发送消息" @tap="ask(input)"><AppIcon name="arrow" :size="31" light /></button></view><text class="composer-note">基于已记录的账单分析，实际收支以你的核对为准</text></view>
    <view v-if="historyOpen" class="sheet-mask" @tap="historyOpen = false"><view class="sheet" @tap.stop><view class="sheet-handle" /><view class="between"><text class="sheet-title">最近的分析对话</text><button class="icon-button" aria-label="关闭历史对话" @tap="historyOpen = false"><AppIcon name="close" :size="30" /></button></view><scroll-view scroll-y class="history-list"><button v-for="item in chat.history" :key="item.id" class="history-item" :disabled="busy" @tap="chooseHistory(item.id)"><text>{{ item.title }}</text><text>{{ monthLabel(item.month) }}</text></button><text v-if="!chat.history.length" class="chat-status">当前账本和月份还没有已保存的对话</text></scroll-view><text class="welcome-note">展示当前账本及入口月份最近二十个对话</text></view></view>
  </view>
</template>

<style scoped lang="scss">
.chat-status { padding: 20rpx 0; font-size: 23rpx; color: #77927e; }
.chat-error { padding: 20rpx; margin-bottom: 24rpx; border-radius: 16rpx; background: #f8eee5; font-size: 24rpx; color: #946449; }
.chat-error > view { display: flex; gap: 24rpx; margin-top: 16rpx; }
.chat-error button, .older-turns { font-size: 23rpx; color: #285e4e; }
.older-turns { display: block; margin: 0 auto 20rpx; }
.history-list { max-height: 55vh; }
.history-item { display: flex; flex-direction: column; gap: 10rpx; padding: 25rpx 0; text-align: left; border-bottom: 1rpx solid #e5ede7; font-size: 27rpx; }
.history-item text:last-child { font-size: 22rpx; color: #8b9d90; }

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
