<script setup lang="ts">
import { useLedgerStore } from '@/store/ledger'
import { ref } from 'vue'
import AppIcon from './AppIcon.vue'
import { activeMembers } from '@/common/services/shared-books'
const store = useLedgerStore()
const open = ref(false)
function choose(id: string) { store.selectBook(id); open.value = false }
</script>

<template>
  <view>
    <button class="book-switcher" @tap="open = true"><AppIcon name="book" :size="27" /><text>{{ store.activeBook.name }}</text><AppIcon name="down" :size="23" /></button>
    <view v-if="open" class="sheet-mask" @tap="open = false"><view class="sheet" @tap.stop><view class="sheet-handle" /><view class="between"><text class="sheet-title">切换账本</text><button class="icon-button" aria-label="关闭账本选择" @tap="open = false"><AppIcon name="close" :size="34" /></button></view><scroll-view scroll-y class="book-options"><button v-for="book in store.books" :key="book.id" class="book-option" @tap="choose(book.id)"><view class="option-icon" :style="{ background: book.color }"><AppIcon :name="book.icon" :size="37" /></view><view class="option-copy"><text>{{ book.name }}</text><text v-if="book.kind === 'shared'" class="option-kind">多人账本 · {{ activeMembers(book).length }} 人一起记</text></view><AppIcon v-if="book.id === store.activeBookId" name="check" :size="32" /></button></scroll-view></view></view>
  </view>
</template>

<style scoped>
.book-switcher { display: flex; align-items: center; gap: 9rpx; background: #fff; border: 1rpx solid #e8eee9; border-radius: 99rpx; padding: 10rpx 17rpx; color: #557060; font-size: 23rpx; }
.book-options { max-height: 55vh; margin-top: 20rpx; }
.book-option { display: flex; align-items: center; gap: 22rpx; width: 100%; text-align: left; padding: 22rpx 0; border-bottom: 1rpx solid #edf2ee; }
.option-copy { flex: 1; min-width: 0; font-size: 28rpx; }
.option-copy text { display: block; overflow-wrap: anywhere; }
.option-copy .option-kind { color: #91a195; font-size: 21rpx; margin-top: 5rpx; }
.option-icon { width: 66rpx; height: 66rpx; border-radius: 21rpx; display: flex; align-items: center; justify-content: center; }
</style>
