<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { onLoad, onShow, onHide, onUnload, onShareAppMessage } from '@dcloudio/uni-app'
import TopBar from '@/components/TopBar.vue'
import { useLedgerStore } from '@/store/ledger'
import type { Book, Entry } from '@/common/types'
import { canEditEntry, entryAuthor } from '@/common/services/shared-books'
import { money } from '@/common/utils/format'
import { appShare } from '@/common/services/share'

onShareAppMessage(appShare)
const store = useLedgerStore()
const id = ref(''), bookId = ref(''), owner = ref('')
const entry = ref<Entry>(), book = ref<Book>()
const loading = ref(false), error = ref('')
let generation = 0, visible = false
const editable = computed(() => !!entry.value && canEditEntry(book.value, entry.value, store.accountId || ''))
onLoad(options => { id.value = options?.id || ''; bookId.value = options?.bookId || ''; owner.value = store.accountId || '' })
onShow(() => { visible = true; void read() })
function hide() { visible = false; generation++; entry.value = undefined; book.value = undefined; loading.value = false }
onHide(hide)
onUnload(hide)
watch([() => store.accountId, () => store.activeBookId, () => store.cloudReady], () => {
  generation++; entry.value = undefined; book.value = undefined; loading.value = false
  error.value = '账号或账本状态已变化，请返回对话重新打开账单'
}, { flush: 'sync' })
async function read(): Promise<boolean> {
  const current = ++generation
  loading.value = true; error.value = ''; entry.value = undefined; book.value = undefined
  try {
    if (!id.value || !bookId.value || owner.value !== store.accountId) throw new Error('账单入口已失效，请返回对话重新打开')
    const result = await store.readEntryReference(id.value, bookId.value)
    if (current !== generation || !visible) return false
    entry.value = result.entry; book.value = result.book
    return true
  } catch (cause) {
    if (current === generation && visible) error.value = cause instanceof Error ? cause.message : '账单读取失败，请重试'
    return false
  } finally { if (current === generation) loading.value = false }
}
async function edit() {
  if (loading.value || !await read() || !editable.value) return
  uni.navigateTo({ url: `/pages/record/index?id=${encodeURIComponent(id.value)}&bookId=${encodeURIComponent(bookId.value)}&fromChat=1` })
}
</script>
<template>
  <view class="entry-page"><view class="header"><TopBar title="账单详情" back /></view>
    <scroll-view scroll-y class="entry-scroll"><view class="content">
      <text v-if="loading" class="status">正在核对当前账单与访问权限…</text>
      <view v-else-if="error" class="error"><text>{{ error }}</text><button @tap="read">重新读取</button></view>
      <view v-else-if="entry && book">
        <text class="scope">{{ book.name }} · {{ entry.type === 'income' ? '收入' : '支出' }}</text><text class="amount number">¥{{ money(entry.amount) }}</text>
        <view class="row"><text>分类</text><text>{{ store.categoryOf(entry.categoryId).name }}</text></view>
        <view class="row"><text>日期</text><text>{{ entry.date }} {{ entry.time || '' }}</text></view>
        <view class="row"><text>账户</text><text>{{ entry.account }}</text></view>
        <view v-if="book.kind === 'shared'" class="row"><text>记账人</text><text>{{ entryAuthor(book, entry) }}</text></view>
        <view class="note"><text class="scope">备注</text><text selectable>{{ entry.note || '未填写备注' }}</text></view>
        <text class="hint">这里显示当前账单，可能与历史回答中的统计快照不同。</text>
        <button v-if="editable" class="edit" :disabled="loading" @tap="edit">编辑这笔账单</button><text v-else class="hint">你已无权修改这个账本的账单，请刷新后重试。</text>
      </view>
    </view></scroll-view>
  </view>
</template>
<style scoped>
.entry-page { height: 100vh; height: 100dvh; display: flex; flex-direction: column; background: #f6f8f7; }
.header { padding: 0 36rpx; }
.entry-scroll { flex: 1; height: 0; min-height: 0; }
.content { padding: 24rpx 36rpx calc(36rpx + env(safe-area-inset-bottom)); }
.scope, .hint, .status { display: block; font-size: 24rpx; color: #687e70; line-height: 1.8; }
.amount { display: block; font-size: 60rpx; margin: 16rpx 0 32rpx; overflow-wrap: anywhere; }
.row { display: flex; justify-content: space-between; gap: 24rpx; padding: 24rpx 0; border-bottom: 1rpx solid #dfe8e1; font-size: 27rpx; }
.row text:last-child { text-align: right; overflow-wrap: anywhere; min-width: 0; }
.note { padding: 24rpx 0; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 27rpx; line-height: 1.9; }
.hint { font-size: 23rpx; margin: 24rpx 0; }
.edit { display: block; background: #285e4e; color: #fff; border-radius: 18rpx; padding: 24rpx; font-size: 27rpx; }
.error { color: #946449; font-size: 26rpx; line-height: 1.8; }
.error button { display: block; margin-top: 24rpx; color: #285e4e; font-size: 26rpx; }
</style>
