<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import AppIcon from '@/components/AppIcon.vue'
import { useLedgerStore } from '@/store/ledger'
import { useRecordStore } from '@/store/record'
import { categories } from '@/common/data/categories'
import { recordExamples } from '@/common/mock/assistant'
import type { AssistantDraft } from '@/common/mock/assistant'
import type { RecordImage } from '@/common/types/record'
import { chooseRecordImage } from '@/common/services/record-image'
import { money, toast } from '@/common/utils/format'

defineOptions({ options: { virtualHost: true } })
const store = useLedgerStore()
const record = useRecordStore()
const input = ref('')
const attachment = ref<RecordImage>()
const choosing = ref(false)
const inputError = ref('')
const anchor = ref('')
const editingKey = ref('')
const form = ref<AssistantDraft & { amountText: string }>()
const accounts = ['微信', '支付宝', '银行卡', '现金']
const visibleCategories = computed(() => categories.filter(item => item.type === form.value?.type))
const busy = computed(() => record.busy || choosing.value)
const blocked = computed(() => record.blocked || choosing.value)
let selectionRevision = 0

function clearComposer() {
  selectionRevision++
  input.value = ''
  attachment.value = undefined
  choosing.value = false
  inputError.value = ''
  editingKey.value = ''
  form.value = undefined
}
watch(() => [store.accountId, store.activeBookId], clearComposer, { flush: 'sync' })
watch(() => [record.messages, record.sending, record.error], async () => {
  anchor.value = ''
  await nextTick()
  anchor.value = 'record-end'
}, { deep: true, flush: 'post' })
onBeforeUnmount(() => { selectionRevision++; record.clear() })

async function send() {
  if (blocked.value) return
  if (!input.value.trim() && !attachment.value) { inputError.value = '写下一笔收支，或选择一张账单图片。'; return }
  inputError.value = ''
  const text = input.value
  const image = attachment.value
  const version = selectionRevision
  input.value = ''
  attachment.value = undefined
  if (!await record.send(text, image) && version === selectionRevision && !record.retry) {
    input.value = text
    attachment.value = image
  }
}
async function chooseImage() {
  if (blocked.value) return
  if (!store.accountId) { inputError.value = '微信登录后，可以拍照或从相册识别账单。'; return }
  const version = ++selectionRevision
  choosing.value = true
  inputError.value = ''
  try {
    const image = await chooseRecordImage()
    if (version === selectionRevision && image) attachment.value = image
  } catch (cause) {
    if (version === selectionRevision) inputError.value = cause instanceof Error ? cause.message : '图片读取失败，请重新选择'
  } finally { if (version === selectionRevision) choosing.value = false }
}
function previewImage(path: string) { uni.previewImage({ urls: [path], current: path }) }
function chooseExample(text: string) { if (!blocked.value) { input.value = text; inputError.value = '' } }
function startNew() {
  if (blocked.value) return
  const reset = () => { record.clear(); clearComposer() }
  if (!record.pending.length && !attachment.value) { reset(); return }
  uni.showModal({ title: '开始新对话', content: '当前未保存的草稿和已选图片会清空，已保存的账单会保留。', confirmText: '开始新对话', cancelText: '继续当前', confirmColor: '#285e4e', success: result => { if (result.confirm) reset() } })
}
function editDraft(draft: AssistantDraft) {
  if (blocked.value) return
  form.value = { ...draft, amountText: (draft.amount / 100).toFixed(2) }
  editingKey.value = draft.key
}
function changeType(event: { detail: { value: string | number } }) {
  if (!form.value) return
  form.value.type = Number(event.detail.value) === 0 ? 'expense' : 'income'
  form.value.categoryId = categories.find(item => item.type === form.value!.type)!.id
}
function changeCategory(event: { detail: { value: string | number } }) { if (form.value) form.value.categoryId = visibleCategories.value[Number(event.detail.value)].id }
function changeAccount(event: { detail: { value: string | number } }) { if (form.value) { form.value.account = accounts[Number(event.detail.value)]; form.value.accountDefaulted = false } }
function changeDate(event: { detail: { value: string } }) { if (form.value) form.value.date = event.detail.value }
function changeTime(event: { detail: { value: string } }) { if (form.value) form.value.time = event.detail.value }
function applyEdit() {
  if (!form.value || blocked.value) return
  if (!/^\d{1,7}(\.\d{1,2})?$/.test(form.value.amountText) || Number(form.value.amountText) <= 0 || Number(form.value.amountText) > 9999999.99) return toast('请输入有效金额，最多两位小数')
  const { amountText, ...updated } = form.value
  record.editDraft(editingKey.value, { ...updated, amount: Math.round(Number(amountText) * 100), note: updated.note.trim() })
  editingKey.value = ''
}
async function confirm(keys: string[]) {
  const count = await record.confirm(keys)
  if (count) toast(`已保存 ${count} 笔${store.accountId ? '到云端账本' : '到账本'}`)
}
function viewLedger() { uni.redirectTo({ url: '/pages/ledger/index' }) }
function login() { uni.navigateTo({ url: '/pages/login/index?redirect=%2Fpages%2Fassistant%2Findex' }) }
</script>

<template>
  <view class="record-panel">
    <view class="record-toolbar"><text>{{ record.pending.length ? `${record.pending.length} 笔待确认 · 可以继续补充` : '文字、截图、小票，都可以发给我' }}</text><button :disabled="blocked" @tap="startNew"><AppIcon name="plus" :size="23" />新对话</button></view>
    <scroll-view scroll-y class="record-scroll" :scroll-into-view="anchor" scroll-with-animation>
      <view class="record-body">
        <view v-if="!record.messages.length" class="record-welcome">
          <view class="welcome-mark"><AppIcon name="edit" :size="43" light /></view>
          <text class="welcome-title">聊两句，把账记好</text>
          <view class="welcome-copy"><text>说一笔收支，或发一张账单图片。</text><text>需要补充的信息，我会接着问你。</text></view>
          <view class="receipt-example"><AppIcon name="image" :size="35" /><view><text>截图、小票，拍下就能记</text><text>识别后先核对，再确认保存</text></view></view>
          <text class="examples-title">也可以从这一句开始</text>
          <button v-for="example in recordExamples" :key="example.label" class="example-row" :disabled="blocked" @tap="chooseExample(example.text)"><text>{{ example.text }}</text><AppIcon name="plus" :size="25" /></button>
          <text class="welcome-note">{{ store.accountId ? '只记录到当前账本；可以继续说“改成32元”或“是昨天的”。' : '当前为预置示例，微信登录后可自由聊天和识别图片。' }}</text>
          <button v-if="!store.accountId" class="login-link" @tap="login">微信登录，使用智能记账<AppIcon name="chevron" :size="22" /></button>
        </view>
        <view v-for="message in record.messages" :key="message.id" class="record-message" :class="message.role">
          <view v-if="message.role === 'user'" class="user-bubble">
            <image v-if="message.imagePath" :src="message.imagePath" class="sent-image" mode="widthFix" @tap="previewImage(message.imagePath)" />
            <text v-if="message.text" class="message-text">{{ message.text }}</text>
            <text v-if="message.failed" class="failed-label">未完成识别</text>
          </view>
          <view v-else class="assistant-message">
            <view class="assistant-author"><AppIcon name="leaf" :size="25" /><text>墨记助手</text><text v-if="!store.accountId" class="demo-tag">示例</text></view>
            <text class="message-text">{{ message.text }}</text>
            <text v-if="message.superseded" class="updated-note">草稿已在后续对话中更新，以最新卡片为准</text>
            <view v-for="draft in message.drafts" :key="draft.key" class="draft-card" :class="{ finished: draft.status !== 'pending' || message.superseded }">
              <view class="between"><view class="draft-category"><view class="draft-icon" :style="{ background: store.categoryOf(draft.categoryId).color }"><AppIcon :name="store.categoryOf(draft.categoryId).icon" :size="32" /></view><view><text class="draft-category-name">{{ store.categoryOf(draft.categoryId).name }}</text><text class="draft-type">{{ draft.type === 'expense' ? '支出' : '收入' }}</text></view></view><text class="draft-amount number" :class="{ income: draft.type === 'income' }">{{ draft.type === 'expense' ? '−' : '+' }}{{ money(draft.amount) }}</text></view>
              <text class="draft-note">{{ draft.note || '未填写备注' }}</text>
              <view class="draft-meta"><text>{{ draft.date }}{{ draft.time ? ` ${draft.time}` : '' }}</text><text>{{ draft.account }}{{ draft.accountDefaulted ? '（默认）' : '' }}</text></view>
              <view v-if="!message.superseded && draft.status === 'pending'" class="draft-actions"><button :disabled="blocked" class="ignore-action" @tap="record.dismissDraft(draft.key)">忽略</button><button :disabled="blocked" class="edit-action" @tap="editDraft(draft)">修改</button><button :disabled="busy" class="confirm-action" @tap="confirm([draft.key])">{{ record.uncertainKeys.includes(draft.key) ? '重试保存' : '确认记账' }}</button></view>
              <view v-else class="draft-state"><AppIcon :name="draft.status === 'saved' ? 'check' : 'info'" :size="23" /><text>{{ draft.status === 'saved' ? '已保存，明细与统计已更新' : draft.status === 'dismissed' ? '已忽略，未保存到账本' : '旧草稿，以后续更新为准' }}</text></view>
            </view>
            <button v-if="message.drafts && !message.superseded && message.drafts.filter(item => item.status === 'pending').length > 1" class="primary-button batch-button" :disabled="busy" :loading="record.saving" @tap="confirm(record.pending.map(item => item.key))">确认全部 {{ record.pending.length }} 笔</button>
          </view>
        </view>
        <view v-if="record.sending" class="recognizing"><view class="thinking-dot" /><text>{{ record.messages[record.messages.length - 1]?.imagePath ? '正在查看图片，整理账单…' : '正在整理，稍等一下…' }}</text></view>
        <view v-if="record.error" class="record-error"><text>{{ record.error }}</text><view class="error-actions"><button v-if="record.retry" :disabled="blocked" @tap="record.retryLast()">重试这条消息</button><button v-if="record.uncertainKeys.length" :disabled="busy" @tap="confirm(record.uncertainKeys)">重试保存</button><button v-if="record.uncertainKeys.length" @tap="viewLedger">查看明细核对</button><button v-if="!store.accountId" @tap="login">微信登录</button></view></view>
        <view id="record-end" class="record-end" />
      </view>
    </scroll-view>
    <view class="record-composer">
      <view v-if="attachment" class="attachment-preview"><image :src="attachment.path" mode="aspectFill" @tap="previewImage(attachment.path)" /><view><text>已选一张账单图片</text><text>可以补充说明，再一起发送</text></view><button aria-label="移除图片" :disabled="busy" @tap="attachment = undefined"><AppIcon name="close" :size="28" /></button></view>
      <text v-if="inputError" class="input-error">{{ inputError }}</text>
      <view class="compose-box"><button class="image-button" aria-label="拍照或从相册选择账单" :disabled="blocked" :loading="choosing" @tap="chooseImage"><AppIcon v-if="!choosing" name="image" :size="37" /></button><view class="record-input-wrap"><textarea v-model="input" class="record-input" :placeholder="record.pending.length ? '继续补充，例如：是昨天的' : '说一笔收支，或发张账单图片'" :maxlength="200" auto-height :disable-default-padding="true" :disabled="busy" :adjust-position="true" :cursor-spacing="24" /></view><button class="send-button" aria-label="发送记账消息" :disabled="blocked || (!input.trim() && !attachment)" @tap="send"><AppIcon name="arrow" :size="30" light /></button></view>
      <text class="composer-note">{{ store.accountId ? '文字、图片及必要上下文将发给模型 · 核对后确认记账' : '预置示例演示 · 登录后支持文字聊天和图片识别' }}</text>
    </view>
    <view v-if="editingKey && form" class="sheet-mask" @tap="editingKey = ''"><view class="sheet edit-sheet" @tap.stop><view class="sheet-handle" /><view class="between"><text class="sheet-title">修改账单草稿</text><button class="icon-button" aria-label="关闭修改草稿" @tap="editingKey = ''"><AppIcon name="close" :size="32" /></button></view><scroll-view scroll-y class="edit-fields">
      <view class="form-row"><text class="form-label">金额（元）</text><input v-model="form.amountText" class="edit-input number" type="digit" :maxlength="10" placeholder="输入金额" /></view>
      <picker :range="['支出', '收入']" :value="form.type === 'expense' ? 0 : 1" @change="changeType"><view class="form-row"><text class="form-label">收支类型</text><text class="form-value">{{ form.type === 'expense' ? '支出' : '收入' }}</text><AppIcon name="chevron" :size="24" /></view></picker>
      <picker :range="visibleCategories" range-key="name" :value="visibleCategories.findIndex(item => item.id === form?.categoryId)" @change="changeCategory"><view class="form-row"><text class="form-label">分类</text><text class="form-value">{{ store.categoryOf(form.categoryId).name }}</text><AppIcon name="chevron" :size="24" /></view></picker>
      <picker mode="date" :value="form.date" start="2000-01-01" end="2100-12-31" @change="changeDate"><view class="form-row"><text class="form-label">日期</text><text class="form-value">{{ form.date }}</text><AppIcon name="chevron" :size="24" /></view></picker>
      <view class="form-row"><text class="form-label">时间</text><picker class="time-picker" mode="time" :value="form.time || '08:00'" @change="changeTime"><text>{{ form.time || '未指定，可选择' }}</text></picker><button v-if="form.time" class="clear-time" aria-label="清除时间" @tap="form.time = undefined"><AppIcon name="close" :size="24" /></button></view>
      <picker :range="accounts" :value="accounts.indexOf(form.account)" @change="changeAccount"><view class="form-row"><text class="form-label">账户</text><text class="form-value">{{ form.account }}</text><AppIcon name="chevron" :size="24" /></view></picker>
      <view class="form-row"><text class="form-label">备注</text><input v-model="form.note" class="edit-input" placeholder="填写备注" :maxlength="80" /></view>
      </scroll-view><button class="primary-button apply-edit" :disabled="blocked" @tap="applyEdit">保存修改</button></view></view>
  </view>
</template>

<style scoped lang="scss">
.record-panel { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.record-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10rpx; margin: 0 36rpx; padding: 6rpx 0 16rpx; border-bottom: 1rpx solid #e4ece6; color: #7d9184; font-size: 21rpx; }
.record-toolbar button { display: flex; align-items: center; gap: 5rpx; font-size: 22rpx; color: #51735e; flex-shrink: 0; }
.record-scroll { flex: 1; height: 0; min-height: 0; }
.record-body { padding: 24rpx 36rpx 0; }
.record-welcome { padding: 10rpx 0 26rpx; }
.welcome-mark { display: flex; align-items: center; justify-content: center; width: 83rpx; height: 83rpx; border-radius: 28rpx 28rpx 28rpx 8rpx; background: #285e4e; margin-bottom: 25rpx; }
.welcome-title { display: block; font-size: 38rpx; font-weight: 600; }
.welcome-copy { display: block; font-size: 26rpx; line-height: 1.85; color: #829587; margin-top: 15rpx; white-space: pre-line; }
.welcome-copy text { display: block; }
.receipt-example { display: flex; align-items: center; gap: 18rpx; border-left: 4rpx solid #9fbea9; padding: 6rpx 0 6rpx 20rpx; margin: 29rpx 0 35rpx; }
.receipt-example text { display: block; font-size: 25rpx; }
.receipt-example text:last-child { font-size: 21rpx; color: #889b8d; margin-top: 8rpx; }
.examples-title { display: block; font-size: 22rpx; color: #879a8b; margin-bottom: 8rpx; }
.example-row { display: flex; align-items: center; justify-content: space-between; gap: 16rpx; width: 100%; text-align: left; font-size: 24rpx; padding: 23rpx 0; border-bottom: 1rpx solid #e1eae3; }
.welcome-note { display: block; font-size: 21rpx; line-height: 1.8; margin-top: 22rpx; color: #8b9d90; }
.login-link { display: flex; align-items: center; gap: 7rpx; color: #285e4e; font-size: 23rpx; margin-top: 20rpx; }
.record-message { margin-bottom: 30rpx; }
.record-message.user { display: flex; justify-content: flex-end; }
.user-bubble { max-width: 88%; background: #dfece3; border-radius: 24rpx 4rpx 24rpx 24rpx; padding: 19rpx 25rpx; }
.message-text { display: block; font-size: 27rpx; line-height: 1.8; white-space: pre-wrap; overflow-wrap: anywhere; }
.sent-image { display: block; width: 320rpx; max-width: 100%; border-radius: 12rpx; margin: 4rpx 0 9rpx; }
.failed-label { display: block; color: #996341; font-size: 20rpx; margin-top: 10rpx; }
.assistant-author { display: flex; align-items: center; gap: 9rpx; font-size: 21rpx; color: #718a79; margin-bottom: 12rpx; }
.demo-tag { margin-left: auto; color: #8d9e92; }
.updated-note { display: block; font-size: 21rpx; color: #85988b; margin-top: 12rpx; }
.draft-card { padding: 24rpx; background: #fff; border: 1rpx solid #dce8df; border-radius: 22rpx; margin-top: 18rpx; }
.draft-category { display: flex; align-items: center; gap: 14rpx; }
.draft-icon { width: 59rpx; height: 59rpx; border-radius: 18rpx; display: flex; align-items: center; justify-content: center; }
.draft-category-name { display: block; font-size: 26rpx; font-weight: 600; }
.draft-type { display: block; color: #7f9587; font-size: 19rpx; margin-top: 3rpx; }
.draft-amount { font-size: 38rpx; }
.income { color: #ad7c43; }
.draft-note { display: block; font-size: 25rpx; margin-top: 21rpx; overflow-wrap: anywhere; }
.draft-meta { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 12rpx; margin-top: 12rpx; font-size: 21rpx; color: #7a9182; }
.draft-actions { display: flex; gap: 16rpx; align-items: center; border-top: 1rpx solid #edf2ee; margin-top: 23rpx; padding-top: 18rpx; }
.ignore-action { margin-right: auto; font-size: 23rpx; color: #829487; padding: 10rpx 4rpx; }
.edit-action { padding: 12rpx 18rpx; color: #5b7c67; font-size: 24rpx; }
.confirm-action { padding: 13rpx 23rpx; background: #285e4e; color: #fff; border-radius: 14rpx; font-size: 24rpx; }
.draft-state { display: flex; align-items: center; gap: 9rpx; margin-top: 20rpx; padding-top: 17rpx; border-top: 1rpx solid #edf2ee; color: #7b9382; font-size: 21rpx; }
.finished { background: #f8faf8; border-color: #e4ece5; }
.batch-button { height: 78rpx; font-size: 25rpx; margin-top: 20rpx; }
.recognizing { display: flex; align-items: center; gap: 12rpx; font-size: 24rpx; color: #6f8c79; margin-bottom: 24rpx; }
.thinking-dot { width: 12rpx; height: 12rpx; background: #739f83; border-radius: 50%; }
.record-error { background: #f8eee5; color: #946449; border-radius: 16rpx; padding: 20rpx; font-size: 23rpx; line-height: 1.8; margin-bottom: 20rpx; }
.error-actions { display: flex; flex-wrap: wrap; gap: 20rpx; margin-top: 12rpx; }
.error-actions button { font-size: 23rpx; color: #285e4e; }
.record-end { height: 16rpx; }
.record-composer { flex-shrink: 0; padding: 18rpx 26rpx calc(14rpx + env(safe-area-inset-bottom)); border-top: 1rpx solid #e2ebe4; background: #f6f8f7; }
.compose-box { display: flex; align-items: flex-end; gap: 12rpx; padding: 13rpx; background: #fff; border: 1rpx solid #dce7df; border-radius: 23rpx; }
.image-button, .send-button { display: flex; align-items: center; justify-content: center; width: 62rpx; height: 62rpx; flex-shrink: 0; border-radius: 17rpx; }
.image-button { background: #eef4ef; }
.send-button { background: #285e4e; }
.send-button[disabled] { background: #bdcfc2; }
/* 留白放在外层，避免原生 textarea 的内边距影响自动高度和文字位置。 */
.record-input-wrap { flex: 1; min-width: 0; min-height: 62rpx; padding: 11rpx 0; display: flex; align-items: center; }
.record-input { display: block; width: 100%; min-width: 0; min-height: 40rpx; max-height: 160rpx; font-size: 25rpx; line-height: 40rpx; padding: 0; }
.composer-note { display: block; text-align: center; font-size: 18rpx; color: #829589; margin-top: 12rpx; }
.attachment-preview { display: flex; align-items: center; gap: 18rpx; margin-bottom: 17rpx; padding: 14rpx; border-radius: 18rpx; background: #e8f0e9; }
.attachment-preview image { width: 80rpx; height: 80rpx; border-radius: 10rpx; }
.attachment-preview > view { flex: 1; }
.attachment-preview text { display: block; font-size: 23rpx; }
.attachment-preview text:last-child { font-size: 20rpx; color: #7d9484; margin-top: 8rpx; }
.attachment-preview button { padding: 12rpx; }
.input-error { display: block; color: #946449; font-size: 22rpx; margin-bottom: 14rpx; }
.edit-fields { max-height: 58vh; }
.edit-input { flex: 1; min-width: 0; text-align: right; font-size: 27rpx; }
.time-picker { flex: 1; text-align: right; font-size: 25rpx; }
.clear-time { display: flex; padding: 10rpx; }
.apply-edit { margin-top: 26rpx; }
</style>
