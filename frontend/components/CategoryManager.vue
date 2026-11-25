<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import PromptSheet from './PromptSheet.vue'
import { useLedgerStore } from '@/store/ledger'
import { categories } from '@/common/data/categories'
import { canWriteBook } from '@/common/services/shared-books'
import type { BookCategory, EntryType } from '@/common/types'
import { toast } from '@/common/utils/format'

const props = defineProps<{ open: boolean; bookId: string }>()
const emit = defineEmits<{ close: [] }>()
const store = useLedgerStore()
const selectedBookId = ref(''), type = ref<EntryType>('expense'), editorOpen = ref(false), busy = ref(false)
const editing = ref<BookCategory>()
const book = computed(() => store.books.find(item => item.id === selectedBookId.value))
const publicCategories = computed(() => categories.filter(item => item.type === type.value))
const customCategories = computed(() => store.customCategories.filter(item => item.bookId === selectedBookId.value && item.type === type.value && !item.isDeleted))
const locked = computed(() => busy.value || store.writing || !canWriteBook(book.value) || (!!store.accountId && !store.cloudReady))
watch(() => props.open, open => { if (open) { selectedBookId.value = props.bookId; editorOpen.value = false } })
watch(() => store.accountId, () => { editorOpen.value = false; emit('close') })
watch(selectedBookId, () => { editorOpen.value = false })
function edit(category?: BookCategory) {
  if (locked.value) return
  editing.value = category ? { ...category } : undefined
  editorOpen.value = true
}
async function save(name: string) {
  if (locked.value) return
  busy.value = true
  try {
    if (await store.saveCategory(selectedBookId.value, type.value, name, editing.value)) { editorOpen.value = false; toast('分类已保存') }
  } finally { busy.value = false }
}
function remove(category: BookCategory) {
  if (locked.value) return
  const snapshot = { ...category }, accountId = store.accountId
  uni.showModal({ title: `删除“${category.name}”？`, content: '删除后不再用于新记账，历史账单与统计保留原分类。', confirmText: '删除', confirmColor: '#ae674e', success: async result => {
    if (!result.confirm || locked.value || accountId !== store.accountId || snapshot.bookId !== selectedBookId.value) return
    busy.value = true
    try { if (await store.deleteCategory(snapshot)) toast('分类已删除') } finally { busy.value = false }
  } })
}
</script>

<template>
  <view v-if="open" class="sheet-mask" @tap="!busy && emit('close')">
    <view class="sheet" @tap.stop>
      <view class="sheet-handle" />
      <view class="between"><text class="sheet-title">收支分类</text><button class="icon-button" aria-label="关闭分类" :disabled="busy" @tap="emit('close')"><AppIcon name="close" :size="34" /></button></view>
      <picker :range="store.books" range-key="name" :disabled="busy || editorOpen" :value="store.books.findIndex(item => item.id === selectedBookId)" @change="selectedBookId = store.books[Number($event.detail.value)].id"><view class="book-picker">{{ book?.name || '请选择账本' }}<AppIcon name="chevron" :size="24" /></view></picker>
      <view class="category-toggle"><button :class="{ active: type === 'expense' }" :disabled="busy || editorOpen" @tap="type = 'expense'">支出分类</button><button :class="{ active: type === 'income' }" :disabled="busy || editorOpen" @tap="type = 'income'">收入分类</button><button v-if="store.accountId" class="refresh" :disabled="busy || store.syncing" @tap="store.refreshCloud()">{{ store.syncing ? '刷新中' : '刷新' }}</button></view>
      <text v-if="store.syncError" class="error">{{ store.syncError }}</text>
      <scroll-view scroll-y class="category-scroll">
        <text class="section-label">公共分类 · 固定</text>
        <view class="public-list"><view v-for="category in publicCategories" :key="category.id" class="public-item"><view :style="{ background: category.color }"><AppIcon :name="category.icon" :size="34" /></view><text>{{ category.name }}</text></view></view>
        <view class="between custom-heading"><text class="section-label">本账本自定义分类</text><button class="add-button" :disabled="locked" @tap="edit()">＋ 新增分类</button></view>
        <text v-if="!customCategories.length" class="empty-copy">还没有自定义分类，可以为这个账本添一个。</text>
        <view v-for="category in customCategories" :key="category.id" class="custom-row"><view class="custom-icon" :style="{ background: category.color }"><AppIcon :name="category.icon" :size="30" /></view><text class="custom-name">{{ category.name }}</text><button :disabled="locked" @tap="edit(category)">修改</button><button class="delete-button" :disabled="locked" @tap="remove(category)">删除</button></view>
      </scroll-view>
      <text class="manager-note">自定义分类仅用于所选账本；共享账本成员可共同维护。</text>
    </view>
  </view>
  <PromptSheet :open="open && editorOpen" :title="editing ? '修改分类名称' : `新增${type === 'expense' ? '支出' : '收入'}分类`" :description="book?.name" placeholder="输入分类名称" :initial="editing?.name || ''" :maxlength="12" :loading="busy" @close="editorOpen = false" @confirm="save" />
</template>

<style scoped lang="scss">
.book-picker { display: flex; align-items: center; justify-content: space-between; background: #f0f4ef; padding: 20rpx; border-radius: 16rpx; font-size: 26rpx; margin: 20rpx 0 10rpx; }
.category-toggle { display: flex; align-items: center; gap: 30rpx; margin-bottom: 24rpx; }
.category-toggle button { color: #87998c; font-size: 26rpx; padding: 12rpx 0; }
.category-toggle button.active { color: #386446; border-bottom: 4rpx solid #6b9874; }
.category-toggle .refresh { margin-left: auto; font-size: 23rpx; }
.category-scroll { max-height: 48vh; }
.section-label { font-size: 24rpx; font-weight: 600; color: #536f5c; }
.public-list { display: flex; flex-wrap: wrap; row-gap: 24rpx; padding: 24rpx 0; }
.public-item { width: 25%; display: flex; flex-direction: column; align-items: center; font-size: 22rpx; gap: 10rpx; }
.public-item > view { width: 70rpx; height: 70rpx; border-radius: 22rpx; display: flex; align-items: center; justify-content: center; }
.custom-heading { padding: 18rpx 0; border-top: 1rpx solid #e7eee7; }
.add-button { color: #386446; font-size: 24rpx; padding: 10rpx; }
.custom-row { display: flex; align-items: center; gap: 16rpx; padding: 18rpx 0; border-bottom: 1rpx solid #eef2ed; }
.custom-icon { width: 56rpx; height: 56rpx; border-radius: 16rpx; display: flex; align-items: center; justify-content: center; }
.custom-name { flex: 1; font-size: 26rpx; overflow-wrap: anywhere; }
.custom-row button { padding: 14rpx 8rpx; font-size: 23rpx; color: #386446; }
.custom-row .delete-button { color: #a36650; }
.manager-note, .empty-copy { display: block; color: #8b9c8f; font-size: 22rpx; line-height: 1.8; padding: 14rpx 0; }
.error { display: block; color: #a36650; font-size: 22rpx; margin-bottom: 16rpx; }
button[disabled] { opacity: .5; }
</style>
