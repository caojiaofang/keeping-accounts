<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { onLoad, onShow, onPullDownRefresh, onShareAppMessage } from '@dcloudio/uni-app'
import TopBar from '@/components/TopBar.vue'
import AppIcon from '@/components/AppIcon.vue'
import MemberAvatar from '@/components/MemberAvatar.vue'
import MonthPicker from '@/components/MonthPicker.vue'
import EntryList from '@/components/EntryList.vue'
import { useLedgerStore } from '@/store/ledger'
import { activeMembers, canManageBook, DEMO_USER_ID, memberTotals, validInvitation } from '@/common/services/shared-books'
import { localDate, money, toast } from '@/common/utils/format'
import type { BookActivity, BookMember, Entry } from '@/common/types'
import CloudStatus from '@/components/CloudStatus.vue'
import { invitationPath } from '@/common/services/invitations'

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
const previousMembers = computed(() => book.value?.members?.filter(member => member.status !== 'active') || [])
const userId = computed(() => store.accountId || DEMO_USER_ID)
const activityOpen = ref(false)
const activities = ref<BookActivity[]>([])
const readingActivities = ref(false)
const moreActivities = ref(false)
const activityError = ref('')
const pending = computed(() => store.writing || store.syncing)
watch(book, current => {
  if (current) return
  activities.value = []
  activityOpen.value = false
  inviting.value = false
  managing.value = false
  invitationToken.value = ''
})


onLoad(options => { bookId.value = options?.id || '' })
onShow(async () => {
  now.value = Date.now()
  if (store.accountId) await store.refreshCloud()
  if (book.value) store.selectBook(bookId.value)
})
onPullDownRefresh(async () => { try { await store.refreshCloud() } finally { uni.stopPullDownRefresh() } })
onShareAppMessage(() => invitationValid.value && store.accountId ? {
  title: `邀请你加入「${book.value?.name || '共同账本'}」`, path: invitationPath(invitationToken.value),
} : { title: '墨记，把日子一笔一笔记好', path: '/pages/login/index' })
function selectAndOpen(path: string) {
  if (!book.value) return
  store.selectBook(book.value.id)
  uni.navigateTo({ url: path })
}
async function openInvite(replace = false) {
  if (pending.value) return
  const created = await store.createInvitation(bookId.value, replace)
  if (!created) return
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
async function revoke() {
  if (!invitation.value) return
  if (await store.revokeInvitation(invitation.value.token)) toast(store.accountId ? '邀请已撤销' : '演示邀请已撤销')
}
function remove(member: BookMember) {
  const version = book.value?.version
  uni.showModal({ title: `移出${member.name}？`, content: store.accountId ? '移出后，该成员将立即失去访问权限，已有邀请失效。历史账单和记账人会保留。' : '该成员的历史账单和记账人信息会保留，已有演示邀请同时失效。', confirmText: '移出成员', confirmColor: '#ae674e', success: async result => {
    if (result.confirm && await store.removeMember(bookId.value, member.userId, version)) { memberFilter.value = ''; toast('成员已移出') }
  } })
}
function filterByMember(userId: string) { memberFilter.value = memberFilter.value === userId ? '' : userId }
function lifecycle(action: 'leave' | 'close' | 'transfer', member?: BookMember) {
  if (!book.value || pending.value) return
  const version = book.value.version
  const text = action === 'leave' ? ['退出这个账本？', '退出后将无法再查看共同数据，你记录的历史账单会保留。'] : action === 'close' ? ['解散这个账本？', '所有成员将无法再查看或新增共同账单。服务端保留历史记录，当前不提供恢复入口。'] : ['确认转让账本？', `转让给${member?.name}后，你将成为普通成员，已有邀请失效。`]
  uni.showModal({ title: text[0], content: text[1], confirmText: action === 'transfer' ? '确认转让' : action === 'close' ? '确认解散' : '确认退出', confirmColor: '#ae674e', success: async result => {
    if (!result.confirm || pending.value) return
    const saved = action === 'transfer' ? await store.transferBook(bookId.value, member!.userId, version) : await store.leaveBook(bookId.value, action === 'close', version)
    if (!saved) return
    managing.value = false
    inviting.value = false
    if (action !== 'transfer') backToBooks()
    toast(action === 'transfer' ? '账本已转让' : action === 'close' ? '账本已解散' : '已退出账本')
  } })
}
async function readActivities(earlier = false) {
  if (readingActivities.value) return
  activityOpen.value = true
  readingActivities.value = true
  activityError.value = ''
  const scope = store.accountId
  try {
    const rows = await store.bookActivities(bookId.value, earlier ? activities.value[activities.value.length - 1]?.id : undefined)
    if (scope !== store.accountId || !book.value) return
    activities.value = earlier ? [...activities.value, ...rows] : rows
    moreActivities.value = rows.length === 20
  } catch (cause) { activityError.value = cause instanceof Error ? cause.message : '操作记录读取失败，请重试' }
  finally { readingActivities.value = false }
}
function actor(item: BookActivity) { return book.value?.members?.find(member => member.userId === item.actorId)?.name || '历史成员' }
function actionLabel(action: string) {
  return ({ create_book: '创建了账本', invite: '生成了邀请', revoke_invite: '撤销了邀请', join: '加入了账本', remove_member: '移出了成员', leave: '退出了账本', transfer: '转让了账本', close: '解散了账本', budget: '调整了共同预算', create_entry: '记了一笔', update_entry: '修改了账单', delete_entry: '删除了账单' } as Record<string, string>)[action] || '更新了账本'
}
function changes(item: BookActivity): string {
  if (item.action === 'budget') return `预算 ${money(Number(item.payload.before))} → ${money(Number(item.payload.after))} 元`
  const before = item.payload.before as Entry | undefined
  const after = item.payload.after as Entry | undefined
  const entry = after || before
  if (!entry || typeof entry !== 'object') return book.value?.members?.find(member => member.userId === item.targetId)?.name || ''
  const lines = [`${entry.date} · ${entry.type === 'income' ? '收入' : '支出'} ${money(entry.amount)} 元 · ${store.categoryOf(entry.categoryId).name}`]
  if (before && after) {
    if (before.amount !== after.amount) lines.push(`金额 ${money(before.amount)} → ${money(after.amount)} 元`)
    if (before.date !== after.date || before.time !== after.time) lines.push(`日期时间 ${before.date} ${before.time || ''} → ${after.date} ${after.time || ''}`)
    if (before.type !== after.type || before.categoryId !== after.categoryId) lines.push(`分类 ${store.categoryOf(before.categoryId).name} → ${store.categoryOf(after.categoryId).name}`)
    if (before.note !== after.note) lines.push(`备注 ${before.note || '无'} → ${after.note || '无'}`)
    if (before.account !== after.account) lines.push(`账户 ${before.account} → ${after.account}`)
  } else if (entry.note) lines.push(entry.note)
  return lines.join('\n')
}
function activityTime(time: number) { const date = new Date(time); return `${localDate(date)} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}` }
function backToBooks() { uni.redirectTo({ url: '/pages/shared-books/index' }) }
</script>

<template>
  <view class="page shared-book-page">
    <TopBar title="共同记账" subtitle="每一笔，都是一起生活的痕迹。" back />
    <CloudStatus />
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
      <scroll-view scroll-x class="member-filters"><view class="filter-line"><button class="member-filter" :class="{ active: !memberFilter }" @tap="memberFilter = ''">全部成员</button><button v-for="member in [...members, ...previousMembers]" :key="member.userId" class="member-filter" :class="{ active: memberFilter === member.userId }" @tap="filterByMember(member.userId)">{{ member.name }}{{ member.status === 'removed' ? '（已移出）' : member.status === 'left' ? '（已退出）' : '' }}</button></view></scroll-view>
      <EntryList v-if="entries.length" :entries="entries" />
      <view v-else class="shared-entry-empty panel"><AppIcon name="note" :size="42" /><text>{{ memberFilter ? '这位成员本月还没有记录' : '本月还没有共同账单' }}</text><text class="small muted">{{ memberFilter ? '切换「全部成员」看看其他记录。' : '买菜或晚餐，都可以成为共同的第一笔。' }}</text></view>
      <text class="shared-demo-note">{{ store.accountId ? '共同数据保存在云端，进入页面或下拉可刷新。记账人不一定是实际付款人。' : '本地演示，成员和账单暂不跨设备同步。' }}</text>
      <button v-if="store.accountId" class="text-button activity-link" @tap="readActivities()">查看操作与修改记录</button>
      <button v-if="store.accountId && !owner" class="text-button lifecycle-action" :disabled="pending" @tap="lifecycle('leave')">退出共同账本</button>
      <button v-if="store.accountId && owner" class="text-button lifecycle-action" :disabled="pending" @tap="lifecycle('close')">解散共同账本</button>
      <view class="shared-footer"><button class="primary-button" @tap="selectAndOpen('/pages/record/index')"><AppIcon name="plus" :size="30" light />记一笔共同账单</button></view>
    </template>
    <view v-else class="empty panel"><AppIcon name="book" :size="50" /><view class="empty-title">找不到这个多人账本</view><text class="small">返回「多人账本」选择已有账本，或创建一本。</text><button class="secondary-button" @tap="backToBooks">查看多人账本</button></view>

    <view v-if="managing && book && owner" class="sheet-mask" @tap="managing = false"><view class="sheet" @tap.stop><view class="sheet-handle" /><view class="between"><text class="sheet-title">管理成员</text><button class="icon-button" aria-label="关闭成员管理" @tap="managing = false"><AppIcon name="close" :size="32" /></button></view><text class="sheet-description">移出成员会保留已有账单，创建者不能被移出。</text><scroll-view scroll-y class="management-list"><view v-for="member in members" :key="member.userId" class="member-row"><MemberAvatar :name="member.name" :color="member.color" :size="62" /><view class="member-info"><text>{{ member.name }}</text><text class="member-copy">{{ member.role === 'owner' ? '创建者' : '成员' }}</text></view><button v-if="store.accountId && member.role !== 'owner'" class="text-button transfer-member" :disabled="pending" @tap="lifecycle('transfer', member)">转让</button><button v-if="member.role !== 'owner' && member.userId !== userId" class="remove-member" :disabled="pending" :aria-label="`移出${member.name}`" @tap="remove(member)">移出</button><text v-else class="small muted">{{ member.userId === userId ? '我' : '创建者' }}</text></view></scroll-view><button class="secondary-button" @tap="managing = false; openInvite()">邀请新成员</button></view></view>
    <view v-if="inviting && book && owner" class="sheet-mask" @tap="inviting = false"><view class="sheet" @tap.stop><view class="sheet-handle" /><view class="between"><text class="sheet-title">邀请家人一起记账</text><button class="icon-button" aria-label="关闭邀请" @tap="inviting = false"><AppIcon name="close" :size="32" /></button></view><text class="sheet-description">加入后可查看共同收支，并记录自己的账单。</text><view class="invitation-card"><AppIcon name="home" :size="48" /><text class="invitation-book-name">{{ book.name }}</text><text class="invitation-status">{{ invitationValid && invitation ? `${store.accountId ? '邀请' : '演示邀请'}有效至 ${localDate(new Date(invitation.expiresAt))}` : '这份邀请已失效' }}</text></view><text class="invitation-demo-copy">{{ store.accountId ? '通过下方按钮发送微信邀请卡片，对方登录并确认后加入；重新生成或撤销后旧邀请失效。' : '这是本机示例邀请，不能发送给其他微信用户。' }}</text><button v-if="store.accountId" class="primary-button preview-invite" open-type="share" :disabled="!invitationValid || pending">发送微信邀请</button><button class="secondary-button preview-invite" :disabled="!invitationValid" @tap="previewInvitation">预览邀请页面</button><view class="invitation-actions"><button class="text-button" :disabled="pending" @tap="openInvite(true)">重新生成</button><button v-if="invitationValid" class="text-button" :disabled="pending" @tap="revoke">撤销邀请</button></view></view></view>
    <view v-if="activityOpen" class="sheet-mask" @tap="activityOpen = false"><view class="sheet" @tap.stop><view class="sheet-handle" /><view class="between"><text class="sheet-title">操作与修改记录</text><button class="icon-button" aria-label="关闭操作记录" @tap="activityOpen = false"><AppIcon name="close" :size="30" /></button></view><scroll-view scroll-y class="activity-list"><view v-for="item in activities" :key="item.id" class="activity-row"><text>{{ actor(item) }} · {{ actionLabel(item.action) }}</text><text class="activity-detail">{{ changes(item) }}</text><text class="small muted">{{ activityTime(item.createdAt) }}</text></view><text v-if="activityError" class="activity-detail">{{ activityError }}</text><button v-if="activityError" class="text-button" @tap="readActivities()">重新读取</button><button v-if="moreActivities" class="text-button" :disabled="readingActivities" @tap="readActivities(true)">查看更早记录</button><text v-if="readingActivities" class="small muted">正在读取…</text><text v-else-if="!activities.length && !activityError" class="small muted">还没有操作记录</text></scroll-view></view></view>
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

<style scoped lang="scss">
.activity-link { margin: 20rpx 0; font-size: 25rpx; }
.lifecycle-action { display: block; color: #ae674e; font-size: 24rpx; margin: 25rpx auto; }
.transfer-member { font-size: 23rpx; margin-right: 20rpx; }
.activity-list { max-height: 55vh; }
.activity-row { display: flex; flex-direction: column; gap: 12rpx; padding: 24rpx 0; border-bottom: 1rpx solid #e4ece6; font-size: 25rpx; }
.activity-detail { display: block; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 23rpx; color: #7c9284; line-height: 1.7; }
</style>
