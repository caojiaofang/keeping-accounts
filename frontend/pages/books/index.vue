<script setup lang="ts">
import { ref } from 'vue'
import TopBar from '@/components/TopBar.vue'
import BottomNav from '@/components/BottomNav.vue'
import AppIcon from '@/components/AppIcon.vue'
import PromptSheet from '@/components/PromptSheet.vue'
import { useLedgerStore } from '@/store/ledger'
import { money, toast } from '@/common/format'
import { activeMembers } from '@/common/shared-books'

const store = useLedgerStore()
store.hydrate()
const creating = ref(false)
function stats(id: string) {
  const entries = store.entries.filter(entry => entry.bookId === id && entry.date.startsWith(store.selectedMonth))
  return { count: store.entries.filter(entry => entry.bookId === id).length, expense: entries.filter(entry => entry.type === 'expense').reduce((sum, entry) => sum + entry.amount, 0), income: entries.filter(entry => entry.type === 'income').reduce((sum, entry) => sum + entry.amount, 0) }
}
function selectBook(id: string) {
  if (store.books.find(book => book.id === id)?.kind === 'shared') { uni.navigateTo({ url: `/pages/shared-book/index?id=${encodeURIComponent(id)}` }); return }
  store.selectBook(id)
  uni.redirectTo({ url: '/pages/ledger/index' })
}
function openSharedBooks() { uni.navigateTo({ url: '/pages/shared-books/index' }) }
function createBook(name: string) {
  if (!name) return toast('给新账本起个名字吧')
  if (store.books.some(book => book.name === name)) return toast('这个账本名字已经使用了')
  store.addBook(name)
  creating.value = false
  toast('新账本已创建')
}
</script>

<template>
  <view class="page">
    <TopBar title="我的账本" subtitle="不同的生活，都有自己的章节。" />
    <view class="books-intro"><view><text class="intro-title">把生活，分开记录。</text><text class="intro-copy">日常的烟火，旅途的风景，家的温度。</text></view><view class="intro-leaf"><AppIcon name="book" :size="65" /></view></view>
    <button class="shared-books-entry" @tap="openSharedBooks"><view class="shared-entry-symbol"><AppIcon name="home" :size="37" /></view><view class="shared-entry-copy"><text>多人账本</text><text>邀请爱人或家人，一起记好小家的日常</text></view><AppIcon name="chevron" :size="27" /></button>
    <view class="section-head"><text class="section-title">全部账本<text class="book-count">{{ store.books.length }}</text></text><text class="small muted">点击账本查看明细</text></view>
    <button v-for="book in store.books" :key="book.id" class="book-card" @tap="selectBook(book.id)">
      <view class="book-cover" :style="{ background: book.color }"><view class="book-spine" /><AppIcon :name="book.icon" :size="50" /><text class="cover-caption">墨记</text></view>
      <view class="book-details"><view class="book-title-row"><text class="book-name">{{ book.name }}</text><view v-if="book.id === store.activeBookId" class="current-badge"><AppIcon name="check" :size="20" /><text>当前使用</text></view></view><text class="book-description">{{ book.kind === 'shared' ? `多人账本 · ${activeMembers(book).length} 人一起记` : book.description }}</text><view class="book-metrics"><view><text class="book-metric-label">本月支出</text><text class="book-metric-value number">{{ money(stats(book.id).expense) }}</text></view><view><text class="book-metric-label">本月收入</text><text class="book-metric-value number">{{ money(stats(book.id).income) }}</text></view></view><view class="book-bottom"><text>已记录 {{ stats(book.id).count }} 笔</text><AppIcon name="arrow" :size="27" /></view></view>
    </button>
    <button class="new-book" @tap="creating = true"><view class="new-book-icon"><AppIcon name="plus" :size="37" /></view><text>创建新账本</text><text class="new-book-copy">开启生活的新篇章</text></button>
    <view class="book-tip"><AppIcon name="shield" :size="28" /><text>每个账本的收支与预算独立统计</text></view>
    <BottomNav active="books" />
    <PromptSheet :open="creating" title="创建新账本" description="为一段生活，留一本专属的记录。" placeholder="例如：装修计划、年度旅行" :maxlength="12" @close="creating = false" @confirm="createBook" />
  </view>
</template>

<style scoped lang="scss">
.books-intro { padding: 31rpx 30rpx; background: #e7efe8; border-radius: 27rpx; display: flex; align-items: center; justify-content: space-between; }
.books-intro > view:first-child { display: flex; flex-direction: column; }
.intro-title { font-size: 32rpx; font-weight: 500; letter-spacing: 1rpx; }
.intro-copy { font-size: 22rpx; color: #7b9180; margin-top: 12rpx; }
.intro-leaf { opacity: .35; transform: rotate(10deg); margin-left: 15rpx; }
.shared-books-entry { display: flex; align-items: center; width: 100%; gap: 16rpx; padding: 25rpx 20rpx; border-bottom: 1rpx solid #dfe8df; text-align: left; }
.shared-entry-symbol { background: #e9eedd; width: 70rpx; height: 70rpx; border-radius: 22rpx; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.shared-entry-copy { flex: 1; min-width: 0; font-size: 28rpx; font-weight: 500; }
.shared-entry-copy > text { display: block; }
.shared-entry-copy > text:last-child { color: #8b9b8e; font-size: 21rpx; font-weight: 400; margin-top: 6rpx; }
.book-count { margin-left: 14rpx; color: #99a59b; font-size: 25rpx; font-weight: 400; }
.book-card { display: flex; width: 100%; padding: 28rpx; background: #fff; border-radius: 27rpx; margin-bottom: 24rpx; text-align: left; gap: 25rpx; }
.book-cover { position: relative; width: 132rpx; height: 195rpx; border-radius: 5rpx 15rpx 15rpx 5rpx; display: flex; flex-direction: column; align-items: center; justify-content: center; flex-shrink: 0; box-shadow: inset -5rpx 0 0 #ffffff60, 4rpx 5rpx 0 #dce4dc60; }
.book-spine { position: absolute; left: 13rpx; height: 100%; width: 1rpx; background: #3e624418; }
.cover-caption { margin-top: 20rpx; color: #5c7762; letter-spacing: 8rpx; font-size: 20rpx; padding-left: 8rpx; }
.book-details { flex: 1; min-width: 0; }
.book-title-row { display: flex; align-items: center; justify-content: space-between; gap: 6rpx; flex-wrap: wrap; }
.book-name { font-size: 30rpx; font-weight: 600; }
.current-badge { display: flex; align-items: center; gap: 3rpx; font-size: 17rpx; color: #6a886f; background: #edf5ee; padding: 3rpx 9rpx; border-radius: 9rpx; }
.book-description { display: block; font-size: 21rpx; color: #9aa59b; margin-top: 6rpx; }
.book-metrics { display: flex; gap: 28rpx; margin-top: 22rpx; }
.book-metrics > view { display: flex; flex-direction: column; flex: 1; }
.book-metric-label { color: #9aa59b; font-size: 20rpx; }
.book-metric-value { font-size: 28rpx; margin-top: 3rpx; }
.book-bottom { border-top: 1rpx solid #eff3ef; margin-top: 16rpx; padding-top: 12rpx; display: flex; justify-content: space-between; color: #9aa89d; font-size: 19rpx; }
.new-book { display: flex; flex-direction: column; align-items: center; justify-content: center; border: 2rpx dashed #cddbd0; padding: 34rpx; border-radius: 27rpx; color: #617e67; }
.new-book-icon { background: #e9f0e8; width: 65rpx; height: 65rpx; border-radius: 50%; display: flex; justify-content: center; align-items: center; margin-bottom: 12rpx; }
.new-book-copy { font-size: 21rpx; color: #9aa89b; margin-top: 6rpx; }
.book-tip { display: flex; gap: 10rpx; align-items: center; justify-content: center; font-size: 21rpx; color: #9ba69c; margin-top: 32rpx; }
</style>
