<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad, onShow } from '@dcloudio/uni-app'
import TopBar from '@/components/TopBar.vue'
import AppIcon from '@/components/AppIcon.vue'
import MemberAvatar from '@/components/MemberAvatar.vue'
import MonthPicker from '@/components/MonthPicker.vue'
import EntryList from '@/components/EntryList.vue'
import { useLedgerStore } from '@/store/ledger'
import { activeMembers, canManageBook, DEMO_USER_ID, memberTotals, validInvitation } from '@/common/services/shared-books'
import { localDate, money, toast } from '@/common/utils/format'
import type { BookMember } from '@/common/types'

const store = useLedgerStore()
store.hydrate()
const bookId = ref('')
const managing = ref(false)
const inviting = ref(false)
const invitationToken = ref('')
const now = ref(Date.now())
const memberFilter = ref('')
const book = computed(() => store.books.find(item => item.id === bookId.value && item.kind === 'shared'))
const members = computed(() => activeMembers(book.value))
const owner = computed(() => canManageBook(book.value))
const totals = computed(() => memberTotals(store.entries, bookId.value, store.selectedMonth))
const entries = computed(() => store.entries.filter(entry => entry.bookId === bookId.value && entry.date.startsWith(store.selectedMonth) && (!memberFilter.value || entry.createdBy === memberFilter.value)))
const invitation = computed(() => store.invitations.find(item => item.token === invitationToken.value))
const invitationValid = computed(() => !!invitation.value && validInvitation(invitation.value, book.value, now.value))
const previousMembers = computed(() => book.value?.members?.filter(member => member.status === 'removed') || [])

onLoad(options => { bookId.value = options?.id || '' })
onShow(() => { now.value = Date.now() })
function selectAndOpen(path: string) {
  if (!book.value) return
  store.selectBook(book.value.id)
  uni.navigateTo({ url: path })
}
function openInvite(replace = false) {
  const created = store.createInvitation(bookId.value, replace)
  if (!created) return toast('只有创建者可以邀请成员')
  now.value = Date.now()
  invitationToken.value = created.token
  inviting.value = true
}
function previewInvitation() {
  now.value = Date.now()
  if (!invitationValid.value) return toast('邀请已失效，请重新生成')
  inviting.value = false
  uni.navigateTo({ url: `/pages/book-invite/index?token=${encodeURIComponent(invitationToken.value)}` })
}
function revoke() {
  if (!invitation.value) return
  store.revokeInvitation(invitation.value.token)
  toast('演示邀请已撤销')
}
function remove(member: BookMember) {
  uni.showModal({ title: `移出${member.name}？`, content: '该成员的历史账单和记账人信息会保留，已有邀请同时失效。当前操作仅影响本地演示。', confirmText: '移出成员', confirmColor: '#ae674e', success: result => {
    if (result.confirm && store.removeMember(bookId.value, member.userId)) { memberFilter.value = ''; toast('成员已移出') }
  } })
}
function filterByMember(userId: string) { memberFilter.value = memberFilter.value === userId ? '' : userId }
function backToBooks() { uni.redirectTo({ url: '/pages/shared-books/index' }) }
</script>

<template>
  <view class="page shared-book-page">
    <TopBar title="共同记账" subtitle="每一笔，都是一起生活的痕迹。" back />
    <template v-if="book">
      <view class="book-heading"><view class="shared-cover"><AppIcon name="home" :size="50" /></view><view class="book-heading-copy"><text class="shared-title">{{ book.name }}</text><text class="shared-subtitle">{{ members.length }} 人共同记账 · {{ owner ? '我是创建者' : '我是成员' }}</text></view></view>
      <view class="shared-context"><MonthPicker v-model="store.selectedMonth" /><button class="text-button" @tap="selectAndOpen('/pages/calendar/index')"><AppIcon name="calendar" :size="28" />收支日历</button></view>
      <view class="shared-summary panel"><view><text>共同支出</text><text class="summary-number number">¥{{ money(totals.expense) }}</text></view><view><text>共同收入</text><text class="summary-number number income">¥{{ money(totals.income) }}</text></view><view class="shared-balance"><text>本月结余</text><text class="number">{{ totals.balance < 0 ? '−' : '' }}¥{{ money(totals.balance) }}</text><text class="summary-count">共 {{ totals.count }} 笔</text></view></view>
      <view class="section-head"><text class="section-title">一起记账的人</text><button v-if="owner" class="text-button manage-members" @tap="managing = true">管理成员</button><text v-else class="small muted">共同查看收支</text></view>
      <view class="members-panel panel">
        <view v-for="member in members" :key="member.userId" class="member-row"><MemberAvatar :name="member.name" :color="member.color" /><view class="member-info"><view class="member-name-row"><text>{{ member.name }}</text><text class="member-role">{{ member.role === 'owner' ? '创建者' : '成员' }}</text></view><text class="member-copy">本月记了 {{ memberTotals(store.entries, book.id, store.selectedMonth, member.userId).count }} 笔</text></view><text class="member-expense number">支出 ¥{{ money(memberTotals(store.entries, book.id, store.selectedMonth, member.userId).expense) }}</text></view>
        <button v-if="owner" class="invite-member" @tap="openInvite()"><view class="invite-plus"><AppIcon name="plus" :size="28" /></view><text>邀请爱人或家人一起记</text><AppIcon name="chevron" :size="25" /></button>
      </view>
      <text class="permission-copy">{{ owner ? '你可以管理成员、设置预算，也可以维护共同账单。' : '你可以查看全部账单，新增、修改和删除自己记录的账单；共同预算由创建者设置。' }}</text>
      <view class="section-head"><text class="section-title">共同的收支</text><button class="text-button" @tap="selectAndOpen('/pages/ledger/index')">查看完整明细<AppIcon name="chevron" :size="22" /></button></view>
      <scroll-view scroll-x class="member-filters"><view class="filter-line"><button class="member-filter" :class="{ active: !memberFilter }" @tap="memberFilter = ''">全部成员</button><button v-for="member in [...members, ...previousMembers]" :key="member.userId" class="member-filter" :class="{ active: memberFilter === member.userId }" @tap="filterByMember(member.userId)">{{ member.name }}{{ member.status === 'removed' ? '（已移出）' : '' }}</button></view></scroll-view>
      <EntryList v-if="entries.length" :entries="entries" />
      <view v-else class="shared-entry-empty panel"><AppIcon name="note" :size="42" /><text>{{ memberFilter ? '这位成员本月还没有记录' : '本月还没有共同账单' }}</text><text class="small muted">{{ memberFilter ? '切换「全部成员」看看其他记录。' : '买菜或晚餐，都可以成为共同的第一笔。' }}</text></view>
      <text class="shared-demo-note">本地演示，成员和账单暂不跨设备同步。</text>
      <view class="shared-footer"><button class="primary-button" @tap="selectAndOpen('/pages/record/index')"><AppIcon name="plus" :size="30" light />记一笔共同账单</button></view>
    </template>
    <view v-else class="empty panel"><AppIcon name="book" :size="50" /><view class="empty-title">找不到这个多人账本</view><text class="small">返回「多人账本」选择已有账本，或创建一本。</text><button class="secondary-button" @tap="backToBooks">查看多人账本</button></view>

    <view v-if="managing && book" class="sheet-mask" @tap="managing = false"><view class="sheet" @tap.stop><view class="sheet-handle" /><view class="between"><text class="sheet-title">管理成员</text><button class="icon-button" aria-label="关闭成员管理" @tap="managing = false"><AppIcon name="close" :size="32" /></button></view><text class="sheet-description">移出成员会保留已有账单，创建者不能被移出。</text><scroll-view scroll-y class="management-list"><view v-for="member in members" :key="member.userId" class="member-row"><MemberAvatar :name="member.name" :color="member.color" :size="62" /><view class="member-info"><text>{{ member.name }}</text><text class="member-copy">{{ member.role === 'owner' ? '创建者' : '成员' }}</text></view><button v-if="member.role !== 'owner' && member.userId !== DEMO_USER_ID" class="remove-member" :aria-label="`移出${member.name}`" @tap="remove(member)">移出</button><text v-else class="small muted">{{ member.userId === DEMO_USER_ID ? '我' : '创建者' }}</text></view></scroll-view><button class="secondary-button" @tap="managing = false; openInvite()">邀请新成员</button></view></view>
    <view v-if="inviting && book" class="sheet-mask" @tap="inviting = false"><view class="sheet" @tap.stop><view class="sheet-handle" /><view class="between"><text class="sheet-title">邀请家人一起记账</text><button class="icon-button" aria-label="关闭邀请" @tap="inviting = false"><AppIcon name="close" :size="32" /></button></view><text class="sheet-description">加入后可查看共同收支，并记录自己的账单。</text><view class="invitation-card"><AppIcon name="home" :size="48" /><text class="invitation-book-name">{{ book.name }}</text><text class="invitation-status">{{ invitationValid && invitation ? `演示邀请有效至 ${localDate(new Date(invitation.expiresAt))}` : '这份演示邀请已失效' }}</text></view><text class="invitation-demo-copy">这是本机邀请预览，暂不能发送给其他微信用户。正式邀请将在微信登录和后端接入后开放。</text><button class="primary-button preview-invite" :disabled="!invitationValid" @tap="previewInvitation">预览邀请页面</button><view class="invitation-actions"><button class="text-button" @tap="openInvite(true)">重新生成</button><button v-if="invitationValid" class="text-button" @tap="revoke">撤销邀请</button></view></view></view>
  </view>
</template>

<style scoped lang="scss">
.book-heading { display: flex; align-items: center; gap: 22rpx; margin: 4rpx 0 30rpx; }
.shared-cover { display: flex; align-items: center; justify-content: center; width: 100rpx; height: 112rpx; background: #e4ecdd; border-radius: 6rpx 20rpx 20rpx 6rpx; border-left: 9rpx solid #d1dfcd; flex-shrink: 0; }
.book-heading-copy { min-width: 0; }
.shared-title { font-size: 33rpx; font-weight: 600; display: block; overflow-wrap: anywhere; }
.shared-subtitle { color: #7e9586; font-size: 23rpx; margin-top: 10rpx; display: block; }
.shared-context { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20rpx; }
.shared-context .text-button { display: flex; gap: 7rpx; font-size: 23rpx; }
.shared-summary { display: flex; flex-wrap: wrap; padding: 28rpx; gap: 22rpx; }
.shared-summary > view { flex: 1; min-width: 0; }
.shared-summary text { font-size: 23rpx; color: #889c8e; }
.shared-summary .summary-number { display: block; font-size: 36rpx; color: #355f49; margin-top: 10rpx; overflow-wrap: anywhere; }
.shared-summary .income { color: #ab824f; }
.shared-summary .shared-balance { display: flex; gap: 15rpx; align-items: baseline; flex-basis: 100%; padding-top: 20rpx; border-top: 1rpx solid #edf2ed; flex-wrap: wrap; }
.shared-balance .number { color: #62816c; font-size: 25rpx; }
.shared-balance .summary-count { margin-left: auto; }
.members-panel { padding: 0 25rpx; }
.member-row { display: flex; align-items: center; padding: 24rpx 0; border-bottom: 1rpx solid #edf2ed; gap: 12rpx; }
.member-info { flex: 1; min-width: 0; font-size: 26rpx; }
.member-name-row { display: flex; align-items: center; flex-wrap: wrap; gap: 12rpx; }
.member-role { color: #819889; background: #f0f5f0; border-radius: 6rpx; font-size: 18rpx; padding: 3rpx 9rpx; }
.member-copy { display: block; color: #91a094; font-size: 21rpx; margin-top: 6rpx; }
.member-expense { font-size: 22rpx; color: #67846e; max-width: 45%; overflow-wrap: anywhere; text-align: right; }
.invite-member { display: flex; align-items: center; gap: 15rpx; padding: 23rpx 0; width: 100%; color: #6a8a74; font-size: 24rpx; }
.invite-member > text { flex: 1; text-align: left; }
.invite-plus { width: 64rpx; height: 64rpx; border: 1rpx dashed #c4d6c7; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin: 0 4rpx; }
.permission-copy { font-size: 21rpx; line-height: 1.8; color: #91a095; display: block; margin: 18rpx 5rpx 0; }
.member-filters { width: 100%; margin-bottom: 23rpx; white-space: nowrap; }
.filter-line { display: inline-flex; gap: 13rpx; padding: 2rpx; }
.member-filter { border: 1rpx solid #dce5dd; color: #829989; border-radius: 99rpx; font-size: 23rpx; padding: 10rpx 22rpx; }
.member-filter.active { background: #e1eee3; border-color: #e1eee3; color: #3b6a4c; }
.shared-entry-empty { padding: 40rpx 20rpx; display: flex; flex-direction: column; align-items: center; gap: 15rpx; text-align: center; color: #728d79; }
.shared-demo-note { display: block; text-align: center; font-size: 21rpx; color: #99a69d; margin-top: 28rpx; }
.shared-footer { position: fixed; bottom: 0; left: 0; right: 0; padding: 18rpx 36rpx calc(20rpx + env(safe-area-inset-bottom)); background: #f6f8f7; border-top: 1rpx solid #e6ece6; z-index: 25; }
.shared-footer .primary-button { font-size: 27rpx; height: 88rpx; }
.management-list { max-height: 48vh; margin: 20rpx 0 28rpx; }
.remove-member { color: #ad725b; font-size: 23rpx; padding: 12rpx; }
.invitation-card { display: flex; align-items: center; flex-direction: column; gap: 14rpx; padding: 30rpx; border-radius: 24rpx; background: #edf4ec; margin: 25rpx 0 18rpx; }
.invitation-book-name { font-size: 30rpx; font-weight: 600; text-align: center; overflow-wrap: anywhere; }
.invitation-status { font-size: 22rpx; color: #7b9683; }
.invitation-demo-copy { display: block; font-size: 23rpx; line-height: 1.8; color: #8b9b8f; margin-bottom: 26rpx; }
.preview-invite { font-size: 27rpx; }
.invitation-actions { display: flex; justify-content: center; gap: 45rpx; margin-top: 15rpx; }
/* #ifdef H5 */
@media (min-width: 700px) { .shared-footer { max-width: 480px; left: 50%; transform: translateX(-50%); } }
/* #endif */
</style>
