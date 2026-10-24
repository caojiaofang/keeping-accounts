<script setup lang="ts">
import { computed, ref } from 'vue'
import TopBar from '@/components/TopBar.vue'
import AppIcon from '@/components/AppIcon.vue'
import MemberAvatar from '@/components/MemberAvatar.vue'
import PromptSheet from '@/components/PromptSheet.vue'
import { useLedgerStore } from '@/store/ledger'
import { activeMembers, canManageBook, FAMILY_INVITATION, memberTotals } from '@/common/shared-books'
import { money, toast } from '@/common/format'

const store = useLedgerStore()
store.hydrate()
const creating = ref(false)
const sharedBooks = computed(() => store.books.filter(book => book.kind === 'shared'))
function openBook(id: string) { uni.navigateTo({ url: `/pages/shared-book/index?id=${encodeURIComponent(id)}` }) }
function createBook(name: string) {
  if (!name) return toast('给共同账本起个名字吧')
  if (store.books.some(book => book.name === name)) return toast('这个账本名字已经使用了')
  const id = store.addBook(name, true)
  creating.value = false
  openBook(id)
}
function experience() { openBook(store.openFamilyExample()) }
function previewJoin() { uni.navigateTo({ url: `/pages/book-invite/index?token=${FAMILY_INVITATION}` }) }
</script>

<template>
  <view class="page page-plain shared-books-page">
    <TopBar title="多人账本" subtitle="一家人的日常，一起记。" back />
    <view class="family-intro">
      <view class="family-portrait"><MemberAvatar name="我" :size="104" /><view class="portrait-partner"><MemberAvatar name="家" color="#f1e3cf" :size="104" /></view><view class="portrait-home"><AppIcon name="home" :size="28" light /></view></view>
      <text class="family-title">把小家的日子，记在一起。</text>
      <text class="family-copy">买菜、房租、周末晚餐，谁记的每一笔都清清楚楚。邀请爱人或家人，共同照顾生活。</text>
      <button class="primary-button create-shared-book" @tap="creating = true"><AppIcon name="plus" :size="30" light />创建多人账本</button>
      <button class="join-example" @tap="previewJoin">体验受邀加入<AppIcon name="chevron" :size="24" /></button>
    </view>
    <view class="demo-notice"><AppIcon name="info" :size="27" /><text>本地演示：暂不支持跨设备邀请与同步。</text></view>
    <view class="section-head"><text class="section-title">一起记的账本</text><text class="small muted">{{ sharedBooks.length }} 本</text></view>
    <button v-for="book in sharedBooks" :key="book.id" class="shared-book-card panel" @tap="openBook(book.id)">
      <view class="between"><view class="shared-name-row"><AppIcon name="home" :size="34" /><text class="shared-name">{{ book.name }}</text></view><AppIcon name="chevron" :size="26" /></view>
      <view class="shared-members"><view class="avatar-stack"><MemberAvatar v-for="member in activeMembers(book).slice(0, 4)" :key="member.userId" :name="member.name" :color="member.color" :size="50" /></view><text>{{ activeMembers(book).length }} 人共同记账</text><text class="role-tag">{{ canManageBook(book) ? '我创建的' : '我加入的' }}</text></view>
      <view class="shared-book-totals"><view><text>本月支出</text><text class="number">¥{{ money(memberTotals(store.entries, book.id, store.selectedMonth).expense) }}</text></view><view><text>本月收入</text><text class="number income-value">¥{{ money(memberTotals(store.entries, book.id, store.selectedMonth).income) }}</text></view></view>
    </button>
    <view v-if="!sharedBooks.length" class="shared-empty"><AppIcon name="book" :size="44" /><text class="empty-heading">共同的第一笔，从这里开始</text><text>创建后邀请家人，或者先看看家庭记账的样子。</text></view>
    <button class="family-example" @tap="experience"><view><text>看看家庭账本示例</text><text class="example-copy">两位成员，三笔生活收支</text></view><AppIcon name="arrow" :size="33" /></button>
    <PromptSheet :open="creating" title="创建多人账本" description="创建后，你可以管理成员和共同预算。" placeholder="例如：我们的小家、家庭日常" :maxlength="12" @close="creating = false" @confirm="createBook" />
  </view>
</template>

<style scoped lang="scss">
.family-intro { background: #e6eee7; border-radius: 32rpx; padding: 36rpx 32rpx 22rpx; }
.family-portrait { display: flex; position: relative; width: 178rpx; margin-bottom: 26rpx; }
.portrait-partner { margin-left: -30rpx; }
.portrait-home { position: absolute; right: 0; bottom: -7rpx; background: #285e4e; width: 43rpx; height: 43rpx; border-radius: 50%; display: flex; align-items: center; justify-content: center; border: 3rpx solid #e6eee7; }
.family-title { display: block; font-size: 36rpx; font-weight: 600; letter-spacing: 1rpx; line-height: 1.6; }
.family-copy { display: block; font-size: 24rpx; color: #728b79; line-height: 1.9; margin: 14rpx 0 28rpx; }
.family-intro .primary-button { height: 88rpx; font-size: 27rpx; }
.join-example { display: flex; justify-content: center; align-items: center; gap: 8rpx; color: #5b7b68; font-size: 23rpx; padding: 22rpx 0 0; }
.demo-notice { display: flex; align-items: flex-start; gap: 10rpx; margin: 22rpx 2rpx 0; font-size: 22rpx; color: #86988b; line-height: 1.7; }
.shared-book-card { width: 100%; text-align: left; padding: 28rpx; margin-bottom: 20rpx; }
.shared-name-row { display: flex; align-items: center; gap: 12rpx; flex: 1; min-width: 0; }
.shared-name { font-size: 29rpx; font-weight: 600; overflow-wrap: anywhere; }
.shared-members { display: flex; align-items: center; flex-wrap: wrap; gap: 12rpx; color: #889b8e; font-size: 21rpx; margin: 20rpx 0 24rpx; }
.avatar-stack { display: flex; padding-left: 10rpx; }
.avatar-stack > view { margin-left: -10rpx; }
.role-tag { background: #edf3ee; border-radius: 7rpx; padding: 3rpx 10rpx; color: #688775; }
.shared-book-totals { display: flex; border-top: 1rpx solid #edf1ed; padding-top: 21rpx; gap: 24rpx; }
.shared-book-totals > view { flex: 1; min-width: 0; }
.shared-book-totals text { display: block; font-size: 21rpx; color: #8a9b90; }
.shared-book-totals .number { font-size: 30rpx; color: #3d6250; margin-top: 5rpx; overflow-wrap: anywhere; }
.shared-book-totals .income-value { color: #ab824d; }
.shared-empty { display: flex; align-items: center; flex-direction: column; padding: 24rpx 10rpx 38rpx; color: #8b9c90; font-size: 23rpx; text-align: center; gap: 12rpx; line-height: 1.8; }
.empty-heading { font-size: 27rpx; color: #617d69; margin-top: 8rpx; }
.family-example { display: flex; align-items: center; justify-content: space-between; width: 100%; padding: 24rpx; border: 1rpx dashed #c8d7cb; border-radius: 20rpx; color: #64816c; text-align: left; font-size: 25rpx; }
.example-copy { display: block; color: #8b9e91; font-size: 21rpx; margin-top: 5rpx; }
</style>
