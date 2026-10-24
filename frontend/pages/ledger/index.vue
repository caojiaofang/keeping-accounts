<script setup lang="ts">
import { onShow, onPullDownRefresh } from '@dcloudio/uni-app'
import { computed, ref, watch } from 'vue'
import TopBar from '@/components/TopBar.vue'
import CloudStatus from '@/components/CloudStatus.vue'
import BottomNav from '@/components/BottomNav.vue'
import AppIcon from '@/components/AppIcon.vue'
import MonthPicker from '@/components/MonthPicker.vue'
import BookSwitcher from '@/components/BookSwitcher.vue'
import EntryList from '@/components/EntryList.vue'
import PromptSheet from '@/components/PromptSheet.vue'
import { useLedgerStore } from '@/store/ledger'
import { categories } from '@/common/data/categories'
import { money, toast } from '@/common/utils/format'
import type { EntryType } from '@/common/types'
import { activeMembers, canManageBook } from '@/common/services/shared-books'

const store = useLedgerStore()
store.hydrate()
onPullDownRefresh(async () => { try { await store.refreshCloud() } finally { uni.stopPullDownRefresh() } })
onShow(() => { void store.refreshCloud() })
const filter = ref<'all' | EntryType>('all')
const keyword = ref('')
const searching = ref(false)
const categoryFilter = ref('')
const categoryOpen = ref(false)
const availableCategories = computed(() => categories.filter(category => filter.value === 'all' || category.type === filter.value))
const budgetOpen = ref(false)
const budgetVersion = ref<number>()
watch(budgetOpen, open => { if (open) budgetVersion.value = store.activeBook.version })
const filterTabs = [{ key: 'all' as const, label: '全部' }, { key: 'expense' as const, label: '支出' }, { key: 'income' as const, label: '收入' }]
const filteredEntries = computed(() => store.monthEntries.filter(entry => {
  const matchesType = filter.value === 'all' || entry.type === filter.value
  const matchesCategory = !categoryFilter.value || entry.categoryId === categoryFilter.value
  const matchesKeyword = `${entry.note}${entry.account}${store.categoryOf(entry.categoryId).name}`.includes(keyword.value.trim())
  return matchesType && matchesCategory && matchesKeyword
}))
const budgetPercent = computed(() => store.activeBook.budget > 0 ? Math.round(store.expense / store.activeBook.budget * 100) : 0)
const remaining = computed(() => store.activeBook.budget - store.expense)
function chooseCategory() {
  categoryOpen.value = true
}
function selectCategory(id: string) { categoryFilter.value = id; categoryOpen.value = false }
function setFilter(value: 'all' | EntryType) { filter.value = value; categoryFilter.value = '' }
function openBudget() { if (!canManageBook(store.activeBook)) return toast('只有账本创建者可以设置共同预算'); budgetOpen.value = true }
async function saveBudget(value: string) {
  if (!/^\d{1,7}(\.\d{1,2})?$/.test(value) || Number(value) <= 0) return toast('请输入有效预算，最多保留两位小数')
  if (!await store.updateBudget(Math.round(Number(value) * 100), budgetVersion.value)) return
  budgetOpen.value = false
  toast('月预算已更新')
}
function viewStats() { uni.redirectTo({ url: '/pages/statistics/index' }) }
function openAssistant(mode: string) { uni.navigateTo({ url: `/pages/assistant/index?mode=${mode}` }) }
function openCalendar() { uni.navigateTo({ url: '/pages/calendar/index' }) }
function openSharedBook() { uni.navigateTo({ url: `/pages/shared-book/index?id=${encodeURIComponent(store.activeBookId)}` }) }
</script>

<template>
  <view class="page">
    <TopBar title="墨记" subtitle="把日子，一笔一笔记好。" brand />
    <CloudStatus />
    <view class="context-row"><MonthPicker v-model="store.selectedMonth" /><BookSwitcher /></view>
    <button v-if="store.activeBook.kind === 'shared'" class="shared-ledger-link" @tap="openSharedBook"><AppIcon name="home" :size="26" /><text>多人账本 · {{ activeMembers(store.activeBook).length }} 人一起记</text><text>成员与邀请</text><AppIcon name="chevron" :size="22" /></button>

    <view class="summary-card">
      <view class="summary-watermark"><AppIcon name="leaf" :size="176" /></view>
      <view class="summary-heading"><text>本月支出</text><view class="summary-tag"><view class="badge-dot" />好好生活，慢慢记录</view></view>
      <view class="expense-total number"><text class="currency">¥</text>{{ money(store.expense) }}</view>
      <view class="summary-bottom">
        <view class="summary-metric"><view class="metric-label"><text>本月收入</text><AppIcon name="down" :size="20" /></view><text class="metric-number number">{{ money(store.income) }}</text></view>
        <view class="metric-divider" />
        <view class="summary-metric"><text class="metric-label">本月结余</text><text class="metric-number number">{{ store.balance < 0 ? '−' : '' }}{{ money(store.balance) }}</text></view>
        <button class="summary-link" aria-label="查看收支统计" @tap="viewStats"><AppIcon name="arrow" :size="32" /></button>
      </view>
    </view>

    <button class="budget-card" @tap="openBudget">
      <view class="budget-symbol"><AppIcon name="wallet" :size="35" /></view>
      <view class="budget-info"><view class="between"><text>月预算</text><text class="budget-remaining">{{ remaining >= 0 ? '还可支出' : '已超预算' }} <text class="number">¥{{ money(remaining) }}</text></text></view><view class="progress-track"><view class="progress-fill" :class="{ exceeded: remaining < 0 }" :style="{ width: `${Math.min(budgetPercent, 100)}%` }" /></view></view>
      <AppIcon name="chevron" :size="24" />
    </button>

    <view class="assistant-entries"><button class="assistant-entry" @tap="openAssistant('record')"><view class="assistant-entry-icon"><AppIcon name="edit" :size="34" /></view><view><text class="assistant-entry-title">一句话记账</text><text class="assistant-entry-copy">随手说一句，轻松记好</text></view><AppIcon name="chevron" :size="23" /></button><view class="assistant-entry-divider" /><button class="assistant-entry" @tap="openAssistant('chat')"><view class="assistant-entry-icon"><AppIcon name="chart" :size="34" /></view><view><text class="assistant-entry-title">聊聊花费</text><text class="assistant-entry-copy">看看钱都花在哪儿了</text></view><AppIcon name="chevron" :size="23" /></button></view>
    <view class="section-head ledger-heading"><view class="row"><text class="section-title">收支明细</text><text class="entry-count">{{ filteredEntries.length }} 笔</text></view><view class="tools"><button class="calendar-shortcut" aria-label="消费日历" @tap="openCalendar"><AppIcon name="calendar" :size="30" /><text>日历</text></button><button class="tool-button" aria-label="搜索账单" @tap="searching = !searching; keyword = ''"><AppIcon name="search" :size="35" /></button><button class="tool-button" aria-label="筛选分类" @tap="chooseCategory"><AppIcon name="filter" :size="37" /><view v-if="categoryFilter" class="filter-dot" /></button></view></view>

    <view v-if="searching" class="search-box"><AppIcon name="search" :size="30" /><input v-model="keyword" placeholder="搜索备注、分类或账户" class="search-input" /><button aria-label="关闭搜索" @tap="searching = false; keyword = ''"><AppIcon name="close" :size="28" /></button></view>
    <view class="filter-row"><view class="filter-tabs"><button v-for="tab in filterTabs" :key="tab.key" class="filter-tab" :class="{ active: filter === tab.key }" @tap="setFilter(tab.key)">{{ tab.label }}</button></view><button v-if="categoryFilter" class="category-tag" @tap="categoryFilter = ''">{{ store.categoryOf(categoryFilter).name }} ×</button><text v-else class="filter-hint">每一笔，都是生活</text></view>
    <EntryList :entries="filteredEntries" />
    <view v-if="filteredEntries.length" class="list-end"><view /><text>日子有迹可循</text><view /></view>
    <BottomNav active="ledger" />
    <PromptSheet :open="budgetOpen" title="设置月预算" description="给支出留一点边界，让生活多一点从容。" placeholder="输入预算金额（元）" :initial="String(store.activeBook.budget / 100)" numeric :maxlength="10" @close="budgetOpen = false" @confirm="saveBudget" />
    <view v-if="categoryOpen" class="sheet-mask" @tap="categoryOpen = false"><view class="sheet" @tap.stop><view class="sheet-handle" /><view class="between"><text class="sheet-title">按分类筛选</text><button class="icon-button" aria-label="关闭分类筛选" @tap="categoryOpen = false"><AppIcon name="close" :size="34" /></button></view><scroll-view scroll-y class="category-options"><button class="all-category" :class="{ picked: !categoryFilter }" @tap="selectCategory('')">全部分类</button><view class="category-options-grid"><button v-for="category in availableCategories" :key="category.id" class="category-option" :class="{ picked: categoryFilter === category.id }" @tap="selectCategory(category.id)"><view :style="{ background: category.color }"><AppIcon :name="category.icon" :size="36" /></view><text>{{ category.name }}</text><text v-if="filter === 'all'" class="category-type">{{ category.type === 'income' ? '收入' : '支出' }}</text></button></view></scroll-view></view></view>
  </view>
</template>

<style scoped lang="scss">
.context-row { display: flex; justify-content: space-between; align-items: center; margin: 2rpx 0 24rpx; }
.shared-ledger-link { display: flex; align-items: center; gap: 9rpx; padding: 0 4rpx 22rpx; font-size: 21rpx; color: #789681; width: 100%; text-align: left; }
.shared-ledger-link > text:first-of-type { flex: 1; }
.summary-card { background: #dfede5; border-radius: 32rpx; padding: 31rpx 32rpx 28rpx; position: relative; overflow: hidden; }
.summary-watermark { position: absolute; right: -20rpx; top: 44rpx; opacity: .055; transform: rotate(-18deg); }
.summary-heading { display: flex; align-items: center; justify-content: space-between; font-size: 25rpx; color: #557061; }
.summary-tag { display: flex; align-items: center; font-size: 19rpx; color: #759280; }
.summary-tag .badge-dot { width: 6rpx; height: 6rpx; margin-right: 8rpx; }
.expense-total { font-size: 78rpx; font-weight: 500; line-height: 1.35; margin: 13rpx 0 28rpx; letter-spacing: -2rpx; }
.currency { font-size: 39rpx; margin-right: 12rpx; font-weight: 400; }
.summary-bottom { display: flex; align-items: center; }
.summary-metric { display: flex; flex-direction: column; flex: 1; }
.metric-label { color: #6f8978; font-size: 21rpx; display: flex; align-items: center; gap: 8rpx; }
.metric-number { margin-top: 5rpx; font-size: 34rpx; font-weight: 500; }
.metric-divider { height: 51rpx; width: 1rpx; background: #b8d0be; margin-right: 40rpx; }
.summary-link { align-self: flex-end; width: 48rpx; height: 48rpx; background: #ffffff60; border-radius: 50%; display: flex; align-items: center; justify-content: center; }
.budget-card { display: flex; align-items: center; gap: 20rpx; background: #fff; width: 100%; padding: 26rpx 25rpx; border-radius: 24rpx; text-align: left; margin-top: 18rpx; }
.budget-symbol { width: 60rpx; height: 60rpx; background: #f0f4ef; border-radius: 18rpx; display: flex; align-items: center; justify-content: center; }
.budget-info { flex: 1; font-size: 23rpx; }
.budget-remaining { color: #88968c; font-size: 21rpx; }
.budget-remaining .number { color: #587563; margin-left: 6rpx; }
.progress-track { height: 7rpx; background: #edf2ed; border-radius: 99rpx; margin-top: 14rpx; overflow: hidden; }
.progress-fill { height: 100%; background: #87b49a; border-radius: 99rpx; }
.exceeded { background: #c89467; }
.ledger-heading { margin-top: 36rpx; margin-bottom: 14rpx; }
.assistant-entries { display: flex; align-items: center; gap: 16rpx; padding: 24rpx 18rpx; background: #eef3ee; border: 1rpx solid #e0e9e1; border-radius: 24rpx; margin-top: 20rpx; }
.assistant-entry { flex: 1; min-width: 0; display: flex; align-items: center; text-align: left; gap: 10rpx; }
.assistant-entry > view:nth-child(2) { flex: 1; min-width: 0; }
.assistant-entry-icon { display: flex; align-items: center; justify-content: center; width: 50rpx; flex-shrink: 0; }
.assistant-entry-title { display: block; font-size: 25rpx; font-weight: 600; }
.assistant-entry-copy { display: block; color: #8a9f8e; font-size: 18rpx; margin-top: 5rpx; }
.assistant-entry-divider { width: 1rpx; height: 54rpx; background: #dce6dc; }
.entry-count { font-size: 21rpx; color: #8e9c93; margin-left: 16rpx; }
.tools { display: flex; gap: 20rpx; }
.calendar-shortcut { display: flex; align-items: center; gap: 6rpx; font-size: 23rpx; color: #69866f; }
.tool-button { position: relative; width: 43rpx; height: 43rpx; display: flex; align-items: center; justify-content: center; }
.filter-dot { position: absolute; width: 10rpx; height: 10rpx; background: #b77e47; right: 0; top: 0; border-radius: 50%; }
.filter-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24rpx; }
.filter-tabs { display: flex; gap: 30rpx; }
.filter-tab { color: #929e96; font-size: 25rpx; padding: 9rpx 0; position: relative; }
.filter-tab.active { color: #2f5f49; font-weight: 600; }
.filter-tab.active::after { content: ''; width: 23rpx; height: 5rpx; border-radius: 4rpx; background: #46785c; position: absolute; top: auto; bottom: 0; left: 50%; transform: translateX(-50%); }
.filter-hint { font-size: 21rpx; color: #a4aea7; }
.category-tag { background: #e4eee7; font-size: 21rpx; border-radius: 99rpx; padding: 4rpx 14rpx; color: #64816c; }
.search-box { display: flex; align-items: center; gap: 14rpx; background: #fff; padding: 18rpx 22rpx; border-radius: 18rpx; margin-bottom: 16rpx; }
.search-input { flex: 1; font-size: 25rpx; }
.list-end { display: flex; align-items: center; justify-content: center; gap: 20rpx; color: #a6b0a8; font-size: 21rpx; padding: 22rpx 0; }
.list-end view { width: 48rpx; height: 1rpx; background: #dce5de; }
.category-options { max-height: 60vh; margin-top: 16rpx; }
.all-category { width: 100%; font-size: 26rpx; background: #f5f8f4; border-radius: 17rpx; padding: 18rpx; margin-bottom: 24rpx; }
.category-options-grid { display: flex; flex-wrap: wrap; row-gap: 24rpx; }
.category-option { width: 25%; display: flex; flex-direction: column; align-items: center; gap: 8rpx; font-size: 23rpx; color: #8d9b91; }
.category-option > view { width: 70rpx; height: 70rpx; border-radius: 22rpx; display: flex; align-items: center; justify-content: center; }
.category-option .category-type { font-size: 18rpx; color: #a3ada5; }
.picked { color: #2e6248; font-weight: 600; }
</style>
