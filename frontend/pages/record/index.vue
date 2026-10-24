<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import TopBar from '@/components/TopBar.vue'
import AppIcon from '@/components/AppIcon.vue'
import { useLedgerStore } from '@/store/ledger'
import { categories } from '@/common/data/categories'
import { localDate, toast } from '@/common/utils/format'
import { isCalendarDate } from '@/common/services/expense-calendar'
import { canEditEntry, entryAuthor } from '@/common/services/shared-books'
import type { EntryType } from '@/common/types'

const store = useLedgerStore()
store.hydrate()
const editingId = ref('')
const type = ref<EntryType>('expense')
const categoryId = ref('food')
const amount = ref('0')
const note = ref('')
const date = ref(localDate())
const time = ref('')
const bookId = ref(store.activeBookId)
const accounts = ['微信', '支付宝', '银行卡', '现金']
const account = ref('微信')
const saving = ref(false)
const originalEntry = computed(() => store.entries.find(entry => entry.id === editingId.value))
const recordedBy = computed(() => originalEntry.value ? entryAuthor(store.books.find(book => book.id === originalEntry.value!.bookId), originalEntry.value) : '我')
const visibleCategories = computed(() => categories.filter(category => category.type === type.value))
const activeBook = computed(() => store.books.find(book => book.id === bookId.value) || store.activeBook)
const keys = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '.', '0', 'delete']

onLoad((options) => {
  if (!options?.id) {
    if (options?.date && isCalendarDate(options.date)) date.value = options.date
    if (options?.type === 'income') changeType('income')
    return
  }
  const entry = store.entries.find(item => item.id === options.id)
  if (!entry) { toast('这笔账单不存在'); return }
  if (!canEditEntry(store.books.find(book => book.id === entry.bookId), entry)) { goBack(); toast('你可以查看共同账单，只有本人或创建者可以修改'); return }
  editingId.value = entry.id
  type.value = entry.type
  categoryId.value = entry.categoryId
  amount.value = String(entry.amount / 100)
  note.value = entry.note
  date.value = entry.date
  time.value = entry.time || ''
  bookId.value = entry.bookId
  account.value = entry.account
})

function changeType(value: EntryType) {
  if (type.value === value) return
  type.value = value
  categoryId.value = categories.find(category => category.type === value)!.id
}
function press(key: string) {
  if (key === 'delete') { amount.value = amount.value.length > 1 ? amount.value.slice(0, -1) : '0'; return }
  if (key === '.') { if (!amount.value.includes('.')) amount.value += '.'; return }
  const parts = amount.value.split('.')
  if (parts[1]?.length >= 2 || (!amount.value.includes('.') && parts[0].length >= 7)) return
  amount.value = amount.value === '0' ? key : amount.value + key
}
function changeDate(event: { detail: { value: string } }) { date.value = event.detail.value }
function changeTime(event: { detail: { value: string } }) { time.value = event.detail.value }
function changeAccount(event: { detail: { value: string | number } }) { account.value = accounts[Number(event.detail.value)] }
function changeBook(event: { detail: { value: string | number } }) { bookId.value = store.books[Number(event.detail.value)].id }
function goBack() {
  if (getCurrentPages().length > 1) uni.navigateBack()
  else uni.redirectTo({ url: '/pages/ledger/index' })
}
function openQuickRecord() { uni.navigateTo({ url: '/pages/assistant/index?mode=record' }) }
function save() {
  if (saving.value) return
  const cents = Math.round(Number(amount.value) * 100)
  if (!Number.isFinite(cents) || cents <= 0) return toast('请输入大于 0 的金额')
  saving.value = true
  const saved = store.saveEntry({ type: type.value, categoryId: categoryId.value, amount: cents, note: note.value.trim(), date: date.value, time: time.value || undefined, bookId: bookId.value, account: account.value }, editingId.value || undefined)
  if (!saved) { saving.value = false; return }
  goBack()
  toast(store.storageAvailable ? (editingId.value ? '账单已更新' : '已记下这一笔') : '已记下，本地空间不足，仅本次运行保留')
}
function remove() {
  uni.showModal({ title: '删除这笔账单？', content: '删除后，本地记录和统计将同步更新。', confirmText: '删除', confirmColor: '#ae674e', success: result => {
    if (result.confirm && store.deleteEntry(editingId.value)) { goBack(); toast('账单已删除') }
  } })
}
</script>

<template>
  <view class="record-page">
    <view class="record-header"><TopBar :title="editingId ? '编辑账单' : '记一笔'" back><button v-if="editingId" class="delete-button" aria-label="删除账单" @tap="remove"><AppIcon name="trash" :size="33" /></button></TopBar></view>
    <scroll-view scroll-y class="record-scroll">
      <view class="record-content">
        <button v-if="!editingId" class="quick-record-link" @tap="openQuickRecord"><AppIcon name="edit" :size="28" /><text>试试一句话记账</text><AppIcon name="chevron" :size="22" /></button>
        <view class="entry-type"><button :class="{ active: type === 'expense' }" @tap="changeType('expense')">支出</button><button :class="{ active: type === 'income' }" @tap="changeType('income')">收入</button></view>
        <view class="amount-area"><text class="amount-caption">{{ type === 'expense' ? '支出金额' : '收入金额' }}</text><view class="amount-value number"><text class="amount-symbol">¥</text><text>{{ amount }}</text><view class="amount-caret" /></view></view>
        <view class="category-grid"><button v-for="category in visibleCategories" :key="category.id" class="category-choice" :class="{ chosen: categoryId === category.id }" @tap="categoryId = category.id"><view class="choice-icon" :style="{ background: categoryId === category.id ? '#dcecdf' : '#f0f3ef' }"><AppIcon :name="category.icon" :size="39" /><view v-if="categoryId === category.id" class="choice-check"><AppIcon name="check" :size="17" light /></view></view><text>{{ category.name }}</text></button></view>
        <view class="record-form">
          <view class="form-row"><AppIcon name="note" :size="33" /><text class="form-label">备注</text><input v-model="note" class="note-input" placeholder="写点什么，留住这一刻" :maxlength="80" /></view>
          <picker mode="date" :value="date" start="2000-01-01" end="2100-12-31" @change="changeDate"><view class="form-row"><AppIcon name="calendar" :size="33" /><text class="form-label">日期</text><text class="form-value">{{ date === localDate() ? '今天' : date }}</text><AppIcon name="chevron" :size="23" /></view></picker>
          <view class="form-row"><AppIcon name="calendar" :size="33" /><text class="form-label">时间</text><picker class="record-time" mode="time" :value="time || '08:00'" @change="changeTime"><text>{{ time || '未指定' }}</text></picker><button v-if="time" class="clear-time" aria-label="清除时间" @tap="time = ''"><AppIcon name="close" :size="24" /></button><AppIcon v-else name="chevron" :size="23" /></view>
          <picker :range="accounts" :value="accounts.indexOf(account)" @change="changeAccount"><view class="form-row"><AppIcon name="wallet" :size="33" /><text class="form-label">账户</text><text class="form-value">{{ account }}</text><AppIcon name="chevron" :size="23" /></view></picker>
          <picker :range="store.books" range-key="name" :value="store.books.findIndex(book => book.id === bookId)" :disabled="!!editingId && activeBook.kind === 'shared'" @change="changeBook"><view class="form-row"><AppIcon name="book" :size="33" /><text class="form-label">账本</text><text class="form-value">{{ activeBook.name }}</text><AppIcon v-if="!editingId || activeBook.kind !== 'shared'" name="chevron" :size="23" /></view></picker>
          <view v-if="activeBook.kind === 'shared'" class="form-row"><AppIcon name="user" :size="33" /><text class="form-label">记账人</text><text class="form-value">{{ recordedBy }}</text></view>
        </view>
      </view>
    </scroll-view>
    <view class="keypad-area"><view class="keypad-left"><button v-for="key in keys" :key="key" :aria-label="key === 'delete' ? '退格' : key" class="key number" @tap="press(key)" @longpress="key === 'delete' ? amount = '0' : undefined"><AppIcon v-if="key === 'delete'" name="back" :size="39" /><text v-else>{{ key }}</text></button></view><view class="keypad-right"><button class="today-key" @tap="date = localDate()"><AppIcon name="calendar" :size="34" /><text>今天</text></button><button class="save-key" :disabled="saving" @tap="save"><AppIcon name="check" :size="44" light /><text>{{ editingId ? '保存修改' : '记好了' }}</text></button></view></view>
  </view>
</template>

<style scoped lang="scss">
.record-page { height: 100vh; height: 100dvh; display: flex; flex-direction: column; overflow: hidden; background: #f6f8f7; }
.record-header { padding: 0 36rpx; flex-shrink: 0; }
.record-scroll { flex: 1; min-height: 0; height: 0; }
.record-content { padding: 0 36rpx 28rpx; }
.quick-record-link { display: flex; align-items: center; gap: 11rpx; color: #6c8c75; background: #e8f0e8; border-radius: 14rpx; padding: 15rpx 19rpx; font-size: 22rpx; margin-bottom: 22rpx; }
.quick-record-link text { flex: 1; text-align: left; }
.record-time { flex: 1; text-align: right; font-size: 27rpx; }
.clear-time { display: flex; padding: 10rpx; }
.delete-button { display: flex; align-items: center; margin-left: 20rpx; }
.entry-type { display: flex; align-items: center; gap: 40rpx; }
.entry-type button { font-size: 31rpx; color: #a1aca3; padding: 0 1rpx 12rpx; position: relative; }
.entry-type button.active { font-weight: 600; color: #285e4e; }
.entry-type button.active::after { content: ''; position: absolute; top: auto; bottom: 0; width: 29rpx; height: 5rpx; border-radius: 5rpx; background: #4f8062; left: 50%; transform: translateX(-50%); }
.amount-area { border-bottom: 1rpx solid #dce6de; padding: 27rpx 0 20rpx; }
.amount-caption { font-size: 22rpx; color: #8e9f92; }
.amount-value { display: flex; align-items: center; font-size: 73rpx; line-height: 1.3; margin-top: 6rpx; min-height: 94rpx; letter-spacing: -1.5rpx; }
.amount-symbol { font-size: 38rpx; margin-right: 16rpx; }
.amount-caret { height: 58rpx; width: 3rpx; margin-left: 9rpx; background: #80a18b; }
.category-grid { display: flex; flex-wrap: wrap; padding: 27rpx 0 8rpx; row-gap: 24rpx; }
.category-choice { width: 25%; display: flex; flex-direction: column; align-items: center; font-size: 21rpx; color: #929e94; }
.choice-icon { width: 75rpx; height: 75rpx; border-radius: 25rpx; display: flex; align-items: center; justify-content: center; position: relative; margin-bottom: 9rpx; }
.chosen { color: #345d43; font-weight: 600; }
.choice-check { position: absolute; right: -2rpx; bottom: -2rpx; width: 26rpx; height: 26rpx; border-radius: 50%; background: #5a8768; border: 3rpx solid #f6f8f7; display: flex; justify-content: center; align-items: center; }
.record-form { margin-top: 16rpx; }
.record-form .form-row { min-height: 87rpx; }
.record-form .form-row:last-child { border-bottom: 1rpx solid #edf1ee; }
.note-input { flex: 1; min-width: 0; text-align: right; font-size: 25rpx; }
.keypad-area { flex-shrink: 0; display: flex; gap: 14rpx; padding: 18rpx 22rpx calc(20rpx + env(safe-area-inset-bottom)); background: #edf2ee; border-top: 1rpx solid #e2e9e1; }
.keypad-left { width: 75%; display: flex; flex-wrap: wrap; gap: 10rpx; }
.key { width: calc((100% - 20rpx) / 3); height: 79rpx; border-radius: 15rpx; background: #fff; font-size: 39rpx; display: flex; align-items: center; justify-content: center; }
.keypad-right { flex: 1; display: flex; flex-direction: column; gap: 10rpx; }
.today-key { height: 79rpx; display: flex; align-items: center; justify-content: center; gap: 8rpx; background: #dfe9df; border-radius: 15rpx; font-size: 23rpx; }
.save-key { flex: 1; display: flex; align-items: center; justify-content: center; flex-direction: column; gap: 17rpx; background: #285e4e; color: #fff; border-radius: 15rpx; font-size: 26rpx; }
</style>
