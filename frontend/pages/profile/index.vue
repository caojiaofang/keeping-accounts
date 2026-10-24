<script setup lang="ts">
import { computed, ref } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import TopBar from '@/components/TopBar.vue'
import BottomNav from '@/components/BottomNav.vue'
import AppIcon from '@/components/AppIcon.vue'
import PromptSheet from '@/components/PromptSheet.vue'
import { useLedgerStore } from '@/store/ledger'
import { useAuthStore } from '@/store/auth'
import { categories } from '@/common/data/categories'
import { money, toast } from '@/common/utils/format'
import { entryAuthor } from '@/common/services/shared-books'

const store = useLedgerStore()
const auth = useAuthStore()
store.hydrate()
const nickname = ref('认真生活的你')
function loadNickname() {
  nickname.value = auth.user ? '墨记用户' : '认真生活的你'
  try { nickname.value = uni.getStorageSync(store.preferenceKey('nickname')) || nickname.value } catch { /* 无本地偏好时使用默认称呼。 */ }
}
loadNickname()
onShow(loadNickname)
const nicknameOpen = ref(false)
const budgetOpen = ref(false)
const categoriesOpen = ref(false)
const categoryType = ref<'expense' | 'income'>('expense')
const visibleCategories = computed(() => categories.filter(category => category.type === categoryType.value))
const recordedDays = computed(() => new Set(store.entries.map(entry => entry.date)).size)
const budgetPercent = computed(() => store.activeBook.budget ? Math.round(store.expense / store.activeBook.budget * 100) : 0)
const menuGroups = [
  [{ key: 'budget', title: '预算设置', icon: 'wallet', description: '让每月支出心中有数' }, { key: 'categories', title: '收支分类', icon: 'tag', description: '生活里的各种小事' }, { key: 'export', title: '导出账单', icon: 'export', description: '复制当前账本的 CSV 数据' }],
  [{ key: 'help', title: '使用帮助', icon: 'help', description: '' }, { key: 'privacy', title: '数据与隐私', icon: 'shield', description: '' }, { key: 'about', title: '关于墨记', icon: 'info', description: 'v0.1.0' }],
]
function saveNickname(value: string) {
  if (!value) return toast('请输入你的昵称')
  try { uni.setStorageSync(store.preferenceKey('nickname'), value) } catch { return toast('本地空间不足，昵称暂未保存') }
  nickname.value = value
  nicknameOpen.value = false
  toast('昵称已更新')
}
function saveBudget(value: string) {
  if (!/^\d{1,7}(\.\d{1,2})?$/.test(value) || Number(value) <= 0) return toast('请输入有效预算，最多保留两位小数')
  if (!store.updateBudget(Math.round(Number(value) * 100))) return
  budgetOpen.value = false
  toast('月预算已更新')
}
function exportEntries() {
  if (!store.bookEntries.length) return toast('当前账本还没有账单可导出')
  const cell = (value: string) => `"${value.replace(/"/g, '""')}"`
  const shared = store.activeBook.kind === 'shared'
  const lines = [`日期,时间,类型,分类,金额（元）,账户,备注${shared ? ',记账人' : ''}`, ...[...store.bookEntries].sort((a, b) => b.date.localeCompare(a.date)).map(entry => [entry.date, entry.time || '', entry.type === 'expense' ? '支出' : '收入', store.categoryOf(entry.categoryId).name, (entry.amount / 100).toFixed(2), entry.account, entry.note, ...(shared ? [entryAuthor(store.activeBook, entry)] : [])].map(cell).join(','))]
  uni.setClipboardData({ data: '\uFEFF' + lines.join('\r\n'), success: () => toast('账单 CSV 已复制，可粘贴到表格中'), fail: () => toast('复制未完成，请重新尝试') })
}
function menuAction(key: string) {
  if (key === 'budget') budgetOpen.value = true
  else if (key === 'categories') categoriesOpen.value = true
  else if (key === 'export') exportEntries()
  else if (key === 'help') uni.showModal({ title: '开始使用墨记', content: '点击底部“记一笔”录入收支；点击明细中的账单可以编辑或删除。顶部可切换月份和账本，统计页会同步展示对应数据。', showCancel: false, confirmText: '知道了', confirmColor: '#285e4e' })
  else if (key === 'privacy') uni.showModal({ title: '数据与隐私', content: '微信登录用于识别你的账号，不获取头像、昵称或手机号。当前账单只保存在此设备，按账号独立保存；体验数据不会自动转入登录账号。云端账单同步尚未开放，清理存储前请先导出记录。', showCancel: false, confirmText: '知道了', confirmColor: '#285e4e' })
  else if (key === 'about') uni.showModal({ title: '墨记', content: '把日子，一笔一笔记好。\n\n版本 0.1.0\n一款简单、安静的日常记账工具。', showCancel: false, confirmText: '继续记录', confirmColor: '#285e4e' })
}
function reset() {
  uni.showModal({ title: '恢复演示数据？', content: '这会覆盖本机现有的账单、自建账本和预算设置。需要保留的账单请先导出。', confirmText: '恢复演示', confirmColor: '#ae674e', success: result => { if (result.confirm) { store.resetDemo(); toast('演示数据已恢复') } } })
}
function openAccount() {
  if (auth.user) nicknameOpen.value = true
  else uni.navigateTo({ url: '/pages/login/index?redirect=%2Fpages%2Fprofile%2Findex' })
}
function logout() {
  if (auth.busy) return
  uni.showModal({
    title: '退出当前账号？',
    content: '本地记录会保留在此账号下，重新登录后可以继续查看。',
    confirmText: '退出登录',
    confirmColor: '#285e4e',
    success: async result => {
      if (!result.confirm) return
      try {
        await auth.logout()
        uni.reLaunch({ url: '/pages/login/index' })
      } catch (cause) { toast(cause instanceof Error ? cause.message : '退出失败，请重新尝试') }
    },
  })
}
</script>

<template>
  <view class="page">
    <TopBar title="我的" subtitle="生活有度，心里有数。" />
    <button class="profile-card" @tap="openAccount"><view class="avatar"><AppIcon name="leaf" :size="57" /><view class="avatar-dot" /></view><view class="profile-info"><view class="nickname">{{ auth.user ? nickname : '登录墨记' }}</view><view class="profile-caption">{{ auth.user ? '今天，也要好好照顾自己。' : '用微信登录，开始你的记录' }}</view><view class="local-badge"><view class="badge-dot" />{{ auth.user ? '微信已登录 · 账单保存在本机' : '正在体验本地记账' }}</view></view><AppIcon :name="auth.user ? 'edit' : 'chevron'" :size="30" /></button>
    <view class="personal-stats panel"><view><text class="stat-value number">{{ recordedDays }}<text class="stat-unit"> 天</text></text><text class="stat-label">记过账的日子</text></view><view class="stat-divider" /><view><text class="stat-value number">{{ store.entries.length }}<text class="stat-unit"> 笔</text></text><text class="stat-label">认真记录的生活</text></view><view class="stat-divider" /><view><text class="stat-value number">{{ store.books.length }}<text class="stat-unit"> 本</text></text><text class="stat-label">专属生活账本</text></view></view>
    <button class="budget-preview" @tap="budgetOpen = true"><view class="budget-preview-top"><view class="row"><AppIcon name="wallet" :size="34" /><text>{{ store.activeBook.name }} · 月预算</text></view><AppIcon name="chevron" :size="26" /></view><view class="between budget-preview-bottom"><text class="budget-preview-amount number"><text>¥ </text>{{ money(store.activeBook.budget, 0) }}</text><text class="budget-progress-text">已使用 {{ budgetPercent }}%</text></view><view class="budget-track"><view :style="{ width: `${Math.min(budgetPercent, 100)}%` }" /></view></button>
    <view v-for="(group, index) in menuGroups" :key="index" class="menu-panel panel"><button v-for="item in group" :key="item.key" class="menu-row" @tap="menuAction(item.key)"><view class="menu-icon"><AppIcon :name="item.icon" :size="35" /></view><view class="menu-copy"><text class="menu-title">{{ item.title }}</text><text v-if="item.description && item.key !== 'about'" class="menu-description">{{ item.description }}</text></view><text v-if="item.key === 'about'" class="version-label">{{ item.description }}</text><AppIcon name="chevron" :size="25" /></button></view>
    <view class="local-notice"><AppIcon name="shield" :size="25" /><text>{{ auth.user ? '记录按账号独立保存在本机，暂未开启云端同步' : '当前为体验模式，演示数据不会转入登录账号' }}</text></view>
    <button v-if="auth.user" class="logout-button" :disabled="auth.busy" :loading="auth.loggingOut" @tap="logout">退出登录</button>
    <button v-else class="reset-button" @tap="reset">恢复演示数据</button>
    <view class="brand-footer"><text class="footer-brand">墨记</text><text>把日子，一笔一笔记好。</text></view>
    <BottomNav active="profile" />
    <PromptSheet :open="nicknameOpen" title="怎么称呼你" placeholder="输入昵称" :initial="nickname" :maxlength="12" @close="nicknameOpen = false" @confirm="saveNickname" />
    <PromptSheet :open="budgetOpen" title="设置月预算" :description="`正在设置「${store.activeBook.name}」的每月预算`" placeholder="输入预算金额（元）" :initial="String(store.activeBook.budget / 100)" numeric :maxlength="10" @close="budgetOpen = false" @confirm="saveBudget" />
    <view v-if="categoriesOpen" class="sheet-mask" @tap="categoriesOpen = false"><view class="sheet" @tap.stop><view class="sheet-handle" /><view class="between"><text class="sheet-title">收支分类</text><button class="icon-button" aria-label="关闭分类" @tap="categoriesOpen = false"><AppIcon name="close" :size="34" /></button></view><view class="category-toggle"><button :class="{ active: categoryType === 'expense' }" @tap="categoryType = 'expense'">支出分类</button><button :class="{ active: categoryType === 'income' }" @tap="categoryType = 'income'">收入分类</button></view><scroll-view scroll-y style="max-height: 50vh;"><view class="category-list"><view v-for="category in visibleCategories" :key="category.id" class="category-item"><view :style="{ background: category.color }"><AppIcon :name="category.icon" :size="39" /></view><text>{{ category.name }}</text></view></view></scroll-view><view class="footnote">让生活的小事，各有所属</view></view></view>
  </view>
</template>

<style scoped lang="scss">
.profile-card { display: flex; align-items: center; width: 100%; text-align: left; gap: 25rpx; padding: 16rpx 0 33rpx; }
.avatar { position: relative; width: 112rpx; height: 112rpx; border-radius: 38rpx; background: #deece1; display: flex; align-items: center; justify-content: center; }
.avatar-dot { position: absolute; bottom: -3rpx; right: -3rpx; width: 26rpx; height: 26rpx; background: #6b9c76; border-radius: 50%; border: 5rpx solid #f6f8f7; }
.profile-info { flex: 1; }
.nickname { font-size: 35rpx; font-weight: 600; }
.profile-caption { font-size: 23rpx; color: #92a094; margin-top: 7rpx; }
.local-badge { display: flex; align-items: center; font-size: 19rpx; color: #719079; margin-top: 12rpx; }
.local-badge .badge-dot { width: 7rpx; height: 7rpx; }
.personal-stats { display: flex; align-items: center; padding: 30rpx 6rpx; }
.personal-stats > view { display: flex; flex-direction: column; align-items: center; flex: 1; }
.stat-value { font-size: 43rpx; }
.stat-unit { font-size: 21rpx; color: #7e9484; }
.stat-label { font-size: 20rpx; color: #97a297; margin-top: 4rpx; }
.personal-stats .stat-divider { flex: none; width: 1rpx; height: 50rpx; background: #e7eee7; }
.budget-preview { text-align: left; background: #e3eee5; border-radius: 26rpx; padding: 26rpx 28rpx; margin: 25rpx 0; width: 100%; }
.budget-preview-top { display: flex; justify-content: space-between; align-items: center; color: #63836a; font-size: 24rpx; }
.budget-preview-top .row { gap: 11rpx; }
.budget-preview-bottom { margin: 15rpx 0; }
.budget-preview-amount { font-size: 45rpx; }
.budget-preview-amount > text { font-size: 27rpx; }
.budget-progress-text { font-size: 22rpx; color: #73917b; }
.budget-track { height: 7rpx; border-radius: 7rpx; background: #cadfcf; overflow: hidden; }
.budget-track view { height: 100%; background: #83ad8d; border-radius: 7rpx; }
.menu-panel { padding: 0 26rpx; margin-bottom: 23rpx; }
.menu-row { display: flex; align-items: center; width: 100%; text-align: left; padding: 24rpx 0; border-bottom: 1rpx solid #eef2ed; gap: 20rpx; }
.menu-row:last-child { border: 0; }
.menu-icon { width: 65rpx; height: 65rpx; display: flex; align-items: center; justify-content: center; background: #f2f5f0; border-radius: 20rpx; }
.menu-copy { flex: 1; display: flex; flex-direction: column; }
.menu-title { font-size: 27rpx; }
.menu-description { font-size: 20rpx; color: #a1aba1; margin-top: 4rpx; }
.version-label { color: #a1aba1; font-size: 23rpx; }
.local-notice { display: flex; align-items: center; justify-content: center; gap: 9rpx; color: #9aa89d; font-size: 21rpx; margin-top: 32rpx; }
.reset-button { margin: 20rpx auto 0; color: #879b8b; font-size: 22rpx; padding: 5rpx; }
button.logout-button { width: 100%; padding: 23rpx 0; margin-top: 22rpx; border: 1rpx solid #dce7df; border-radius: 20rpx; font-size: 25rpx; color: #65816d; }
.brand-footer { display: flex; flex-direction: column; align-items: center; margin-top: 34rpx; color: #a8b3a9; font-size: 20rpx; gap: 6rpx; }
.footer-brand { letter-spacing: 6rpx; font-size: 28rpx; color: #91a294; padding-left: 6rpx; }
.category-toggle { display: flex; gap: 30rpx; margin: 15rpx 0 27rpx; }
.category-toggle button { color: #98a59b; font-size: 26rpx; padding: 10rpx 0; }
.category-toggle button.active { color: #386446; border-bottom: 4rpx solid #6b9874; }
.category-list { display: flex; flex-wrap: wrap; row-gap: 28rpx; }
.category-item { width: 25%; display: flex; flex-direction: column; align-items: center; font-size: 22rpx; gap: 10rpx; }
.category-item > view { width: 77rpx; height: 77rpx; border-radius: 24rpx; display: flex; align-items: center; justify-content: center; }
</style>
