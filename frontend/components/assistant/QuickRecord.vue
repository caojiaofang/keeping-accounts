<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import AppIcon from '@/components/AppIcon.vue'
import { useLedgerStore } from '@/store/ledger'
import { categories } from '@/common/data/categories'
import { createExampleDrafts, recordExamples } from '@/common/mock/assistant'
import type { AssistantDraft } from '@/common/mock/assistant'
import { money, toast } from '@/common/utils/format'

defineOptions({ options: { virtualHost: true } })
const store = useLedgerStore()
const input = ref('')
const source = ref('')
const error = ref('')
const drafts = ref<AssistantDraft[]>([])
const anchor = ref('')
const editingKey = ref('')
const form = ref<AssistantDraft & { amountText: string }>()
const accounts = ['微信', '支付宝', '银行卡', '现金']
const visibleCategories = computed(() => categories.filter(item => item.type === form.value?.type))
const pending = computed(() => drafts.value.filter(item => item.status === 'pending'))
const savedCount = computed(() => drafts.value.filter(item => item.status === 'saved').length)

async function recognize() {
  if (!input.value.trim()) { error.value = '先写下一笔收支，或点选下方示例。'; return }
  if (pending.value.length) {
    uni.showModal({ title: '还有待确认的账单', content: '请先保存或忽略当前草稿，再生成新的账单。', showCancel: false, confirmText: '知道了', confirmColor: '#285e4e' })
    return
  }
  const example = createExampleDrafts(input.value, store.activeBookId)
  if (!example) { error.value = '当前为静态演示，请选择下方示例体验。接入大模型后将支持自由输入。'; return }
  error.value = ''
  source.value = input.value.trim()
  drafts.value = example
  anchor.value = ''
  await nextTick()
  anchor.value = 'draft-results'
}
function chooseExample(text: string) { input.value = text; error.value = '' }
function editDraft(draft: AssistantDraft) {
  form.value = { ...draft, amountText: (draft.amount / 100).toFixed(2) }
  editingKey.value = draft.key
}
function changeType(event: { detail: { value: string | number } }) {
  if (!form.value) return
  form.value.type = Number(event.detail.value) === 0 ? 'expense' : 'income'
  form.value.categoryId = categories.find(item => item.type === form.value!.type)!.id
}
function changeCategory(event: { detail: { value: string | number } }) { if (form.value) form.value.categoryId = visibleCategories.value[Number(event.detail.value)].id }
function changeAccount(event: { detail: { value: string | number } }) { if (form.value) form.value.account = accounts[Number(event.detail.value)] }
function changeDate(event: { detail: { value: string } }) { if (form.value) form.value.date = event.detail.value }
function changeTime(event: { detail: { value: string } }) { if (form.value) form.value.time = event.detail.value }
function applyEdit() {
  if (!form.value) return
  if (!/^\d{1,7}(\.\d{1,2})?$/.test(form.value.amountText) || Number(form.value.amountText) <= 0) return toast('请输入有效金额，最多两位小数')
  const draft = drafts.value.find(item => item.key === editingKey.value)
  if (!draft || draft.status !== 'pending') return
  const { amountText, ...updated } = form.value
  Object.assign(draft, updated, { amount: Math.round(Number(amountText) * 100), note: updated.note.trim() })
  editingKey.value = ''
}
function saveDraft(draft: AssistantDraft) {
  if (draft.status !== 'pending') return false
  const { key, status, ...entry } = draft
  if (!store.saveEntry(entry)) return false
  draft.status = 'saved'
  return true
}
function confirmOne(draft: AssistantDraft) { if (saveDraft(draft)) toast(store.storageAvailable ? '已保存到本地账本' : '已记下，本次仅在运行中保留') }
function confirmAll() {
  const results = pending.value.map(saveDraft)
  if (results.every(Boolean)) toast(store.storageAvailable ? '账单已保存到本地' : '已记下，本次仅在运行中保留')
}
function viewLedger() { uni.redirectTo({ url: '/pages/ledger/index' }) }
</script>

<template>
  <scroll-view scroll-y class="quick-scroll" :scroll-into-view="anchor" scroll-with-animation>
    <view class="quick-body">
      <view class="quick-intro"><view class="intro-mark"><AppIcon name="edit" :size="42" /></view><view><text class="intro-title">随手说一句，记下生活</text><text class="intro-copy">金额、分类和时间，都放进一张账单。</text></view></view>
      <view class="input-panel">
        <view class="between"><text class="input-label">这笔钱，花在哪儿了？</text><text class="input-count">{{ input.length }}/200</text></view>
        <textarea v-model="input" class="record-textarea" placeholder="例如：今天早上八点吃早餐8块钱" :maxlength="200" :auto-height="false" :adjust-position="true" :cursor-spacing="28" />
        <view class="input-footer"><text>也可以一次记下多笔收支</text><AppIcon name="note" :size="26" /></view>
        <button class="primary-button recognize-button" @tap="recognize"><AppIcon name="check" :size="30" light />识别账单</button>
      </view>
      <view v-if="error" class="inline-error"><AppIcon name="info" :size="27" /><text>{{ error }}</text></view>
      <view class="example-heading"><text>选一句，体验一下</text><text class="example-hint">预置示例</text></view>
      <button v-for="example in recordExamples" :key="example.label" class="example-row" @tap="chooseExample(example.text)"><text class="example-label">{{ example.label }}</text><text class="example-text">{{ example.text }}</text><AppIcon name="plus" :size="25" /></button>

      <view v-if="drafts.length" id="draft-results" class="draft-results">
        <view class="between result-heading"><text class="section-title">{{ pending.length ? '核对一下，再记好' : '这一笔，安排好了' }}</text><text class="result-count">{{ drafts.length }} 笔账单</text></view>
        <view class="source-text">“{{ source }}”</view>
        <view v-for="draft in drafts" :key="draft.key" class="draft-card" :class="{ finished: draft.status !== 'pending' }">
          <view class="between"><view class="draft-category"><view class="draft-icon" :style="{ background: store.categoryOf(draft.categoryId).color }"><AppIcon :name="store.categoryOf(draft.categoryId).icon" :size="35" /></view><view><text class="draft-category-name">{{ store.categoryOf(draft.categoryId).name }}</text><text class="draft-type">{{ draft.type === 'expense' ? '支出' : '收入' }}</text></view></view><text class="draft-amount number" :class="{ income: draft.type === 'income' }">{{ draft.type === 'expense' ? '−' : '+' }}{{ money(draft.amount) }}</text></view>
          <view class="draft-note">{{ draft.note || '未填写备注' }}</view>
          <view class="draft-meta"><view><AppIcon name="calendar" :size="24" /><text>{{ draft.date }}{{ draft.time ? ` ${draft.time}` : '' }}</text></view><view><AppIcon name="wallet" :size="24" /><text>{{ draft.account }}</text></view></view>
          <view class="draft-book">存入「{{ store.books.find(book => book.id === draft.bookId)?.name || '当前账本' }}」{{ source.includes('银行卡') ? '' : ' · 账户使用默认选项，可修改' }}</view>
          <view v-if="draft.status === 'pending'" class="draft-actions"><button class="ignore-action" @tap="draft.status = 'dismissed'">忽略</button><button class="edit-action" @tap="editDraft(draft)"><AppIcon name="edit" :size="24" />修改</button><button class="confirm-action" @tap="confirmOne(draft)">确认记账</button></view>
          <view v-else class="draft-state"><AppIcon :name="draft.status === 'saved' ? 'check' : 'close'" :size="25" /><text>{{ draft.status === 'saved' ? '已保存，本地明细与统计已更新' : '已忽略，未保存到账本' }}</text></view>
        </view>
        <button v-if="pending.length > 1" class="primary-button batch-button" @tap="confirmAll">确认全部 {{ pending.length }} 笔</button>
        <button v-if="savedCount" class="secondary-button view-ledger" @tap="viewLedger">查看账本明细<AppIcon name="arrow" :size="28" /></button>
      </view>
      <view class="demo-note"><AppIcon name="shield" :size="25" /><text>当前使用预置示例，确认后仅保存到本机。</text></view>
    </view>
    <view v-if="editingKey && form" class="sheet-mask" @tap="editingKey = ''"><view class="sheet edit-sheet" @tap.stop><view class="sheet-handle" /><view class="between"><text class="sheet-title">修改账单草稿</text><button class="icon-button" aria-label="关闭修改草稿" @tap="editingKey = ''"><AppIcon name="close" :size="32" /></button></view><scroll-view scroll-y class="edit-fields">
      <view class="form-row"><text class="form-label">金额（元）</text><input v-model="form.amountText" class="edit-input number" type="digit" :maxlength="10" placeholder="输入金额" /></view>
      <picker :range="['支出', '收入']" :value="form.type === 'expense' ? 0 : 1" @change="changeType"><view class="form-row"><text class="form-label">收支类型</text><text class="form-value">{{ form.type === 'expense' ? '支出' : '收入' }}</text><AppIcon name="chevron" :size="24" /></view></picker>
      <picker :range="visibleCategories" range-key="name" :value="visibleCategories.findIndex(item => item.id === form?.categoryId)" @change="changeCategory"><view class="form-row"><text class="form-label">分类</text><text class="form-value">{{ store.categoryOf(form.categoryId).name }}</text><AppIcon name="chevron" :size="24" /></view></picker>
      <picker mode="date" :value="form.date" start="2000-01-01" end="2100-12-31" @change="changeDate"><view class="form-row"><text class="form-label">日期</text><text class="form-value">{{ form.date }}</text><AppIcon name="chevron" :size="24" /></view></picker>
      <view class="form-row"><text class="form-label">时间</text><picker class="time-picker" mode="time" :value="form.time || '08:00'" @change="changeTime"><text>{{ form.time || '未指定，可选择' }}</text></picker><button v-if="form.time" class="clear-time" aria-label="清除时间" @tap="form.time = ''"><AppIcon name="close" :size="24" /></button></view>
      <picker :range="accounts" :value="accounts.indexOf(form.account)" @change="changeAccount"><view class="form-row"><text class="form-label">账户</text><text class="form-value">{{ form.account }}</text><AppIcon name="chevron" :size="24" /></view></picker>
      <view class="form-row"><text class="form-label">备注</text><input v-model="form.note" class="edit-input" placeholder="填写备注" :maxlength="80" /></view>
      </scroll-view><button class="primary-button apply-edit" @tap="applyEdit">保存修改</button></view></view>
  </scroll-view>
</template>

<style scoped lang="scss">
.quick-scroll { flex: 1; height: 0; min-height: 0; }
.quick-body { padding: 12rpx 36rpx calc(36rpx + env(safe-area-inset-bottom)); }
.quick-intro { display: flex; align-items: center; gap: 20rpx; padding: 12rpx 0 30rpx; }
.intro-mark { width: 76rpx; height: 76rpx; background: #dfede4; border-radius: 25rpx 25rpx 25rpx 8rpx; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.intro-title { display: block; font-size: 32rpx; font-weight: 600; }
.intro-copy { display: block; font-size: 22rpx; color: #8b9c90; margin-top: 8rpx; }
.input-panel { background: #fff; padding: 27rpx; border-radius: 27rpx; border: 1rpx solid #e3ece5; }
.input-label { font-size: 23rpx; color: #7d9184; }
.input-count { font-size: 20rpx; color: #a2aea5; }
.record-textarea { width: 100%; height: 160rpx; margin-top: 24rpx; font-size: 30rpx; line-height: 1.75; }
.input-footer { display: flex; align-items: center; justify-content: space-between; font-size: 21rpx; color: #9ba89e; padding: 12rpx 0 22rpx; }
.recognize-button { height: 84rpx; border-radius: 18rpx; font-size: 27rpx; }
.inline-error { display: flex; align-items: flex-start; gap: 10rpx; padding: 20rpx 0 4rpx; color: #a46a43; font-size: 23rpx; }
.inline-error text { flex: 1; }
.example-heading { display: flex; align-items: center; justify-content: space-between; margin: 29rpx 0 14rpx; font-size: 24rpx; color: #6a8172; }
.example-hint { color: #a1ada4; font-size: 20rpx; }
.example-row { width: 100%; text-align: left; display: flex; align-items: center; gap: 13rpx; padding: 19rpx 0; border-bottom: 1rpx solid #e6ece7; }
.example-label { background: #e9f0e9; color: #738c78; font-size: 18rpx; padding: 5rpx 10rpx; border-radius: 8rpx; flex-shrink: 0; }
.example-text { flex: 1; font-size: 23rpx; color: #61776a; }
.draft-results { padding-top: 35rpx; }
.result-count { font-size: 21rpx; color: #7c9183; }
.source-text { font-size: 23rpx; color: #8e9c92; padding: 13rpx 0 22rpx; overflow-wrap: anywhere; }
.draft-card { padding: 26rpx; background: #fff; border: 1rpx solid #dce8df; border-radius: 26rpx; margin-bottom: 18rpx; }
.draft-category { display: flex; align-items: center; gap: 15rpx; }
.draft-icon { width: 64rpx; height: 64rpx; border-radius: 20rpx; display: flex; justify-content: center; align-items: center; }
.draft-category-name { display: block; font-size: 27rpx; font-weight: 600; }
.draft-type { display: block; color: #9baa9e; font-size: 19rpx; margin-top: 2rpx; }
.draft-amount { font-size: 41rpx; }
.income { color: #ad7c43; }
.draft-note { font-size: 27rpx; margin-top: 24rpx; overflow-wrap: anywhere; }
.draft-meta { display: flex; flex-wrap: wrap; gap: 18rpx; margin-top: 13rpx; font-size: 21rpx; color: #819488; }
.draft-meta > view { display: flex; align-items: center; gap: 8rpx; }
.draft-book { margin-top: 13rpx; font-size: 20rpx; color: #a1ada4; }
.draft-actions { display: flex; gap: 18rpx; align-items: center; border-top: 1rpx solid #edf2ee; margin-top: 25rpx; padding-top: 21rpx; }
.ignore-action { font-size: 23rpx; color: #9aa69d; padding: 10rpx 6rpx; margin-right: auto; }
.edit-action { display: flex; align-items: center; gap: 8rpx; padding: 12rpx 18rpx; font-size: 24rpx; color: #5b7c67; }
.confirm-action { padding: 13rpx 25rpx; background: #285e4e; color: #fff; border-radius: 14rpx; font-size: 24rpx; }
.draft-state { display: flex; align-items: center; gap: 10rpx; border-top: 1rpx solid #edf2ee; margin-top: 22rpx; padding-top: 19rpx; color: #72917b; font-size: 22rpx; }
.finished { border-color: #e4ece5; }
.batch-button { margin-top: 22rpx; }
.view-ledger { margin-top: 20rpx; gap: 15rpx; font-size: 25rpx; }
.demo-note { display: flex; align-items: center; justify-content: center; gap: 10rpx; font-size: 20rpx; color: #9aaa9e; padding-top: 32rpx; }
.edit-fields { max-height: 58vh; }
.edit-input { flex: 1; min-width: 0; text-align: right; font-size: 27rpx; }
.time-picker { flex: 1; text-align: right; font-size: 25rpx; }
.clear-time { display: flex; padding: 10rpx; }
.apply-edit { margin-top: 26rpx; }
</style>
