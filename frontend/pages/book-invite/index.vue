<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { onLoad, onShow } from '@dcloudio/uni-app'
import TopBar from '@/components/TopBar.vue'
import AppIcon from '@/components/AppIcon.vue'
import MemberAvatar from '@/components/MemberAvatar.vue'
import CloudStatus from '@/components/CloudStatus.vue'
import { useLedgerStore } from '@/store/ledger'
import { useAuthStore } from '@/store/auth'
import { activeMembers, DEMO_USER_ID, FAMILY_INVITATION, familyPartner, validInvitation } from '@/common/services/shared-books'
import { forgetInvitation, invitationPath, isCloudInvitation, previewInvitation, rememberInvitation } from '@/common/services/invitations'
import { localDate, toast } from '@/common/utils/format'
import type { InvitationPreview } from '@/common/types'

const store = useLedgerStore()
const auth = useAuthStore()
store.hydrate()
const token = ref('')
const joining = ref(false)
const loading = ref(false)
const error = ref('')
const now = ref(Date.now())
const preview = ref<InvitationPreview>()
const name = ref('家庭成员')
const isExample = computed(() => token.value === FAMILY_INVITATION)
const invitation = computed(() => store.invitations.find(item => item.token === token.value))
const book = computed(() => isExample.value ? store.books.find(item => item.id === 'shared-joined-example') : store.books.find(item => item.id === (preview.value?.bookId || invitation.value?.bookId)))
const valid = computed(() => isExample.value || (isCloudInvitation(token.value) ? !!preview.value && preview.value.expiresAt > now.value : !!invitation.value && validInvitation(invitation.value, book.value, now.value)))
const joined = computed(() => activeMembers(book.value).some(member => member.userId === (store.accountId || DEMO_USER_ID)))
const inviter = computed(() => isExample.value ? familyPartner : { name: preview.value?.inviterName || '创建者', color: '#dcece1' })
const title = computed(() => preview.value?.bookName || book.value?.name || '受邀家庭账本（示例）')
let revision = 0

async function load() {
  now.value = Date.now()
  if (!isCloudInvitation(token.value)) return
  const version = ++revision
  loading.value = true
  error.value = ''
  try {
    const result = await previewInvitation(token.value)
    if (version === revision) preview.value = result
  } catch (cause) {
    if (version === revision) { preview.value = undefined; error.value = cause instanceof Error ? cause.message : '邀请读取失败，请重试' }
  } finally { if (version === revision) loading.value = false }
}
function loadName() {
  try { name.value = String(uni.getStorageSync(store.preferenceKey('nickname')) || '家庭成员').slice(0, 12) } catch { name.value = '家庭成员' }
}
watch(() => store.accountId, loadName)
onLoad(options => { token.value = options?.token || ''; rememberInvitation(token.value); loadName() })
onShow(async () => {
  void load()
  await auth.bootstrap()
  if (store.accountId) await store.refreshCloud()
})
async function accept() {
  if (joining.value || loading.value) return
  now.value = Date.now()
  if (!valid.value) return toast('邀请已失效，请联系创建者重新邀请')
  if (isCloudInvitation(token.value)) {
    await auth.bootstrap()
    if (!auth.isLoggedIn) {
      rememberInvitation(token.value)
      uni.navigateTo({ url: `/pages/login/index?redirect=${encodeURIComponent(invitationPath(token.value))}` })
      return
    }
    if (!name.value.trim()) return toast('请输入你在这个账本中的称呼')
    joining.value = true
    try {
      // 加入必须由本次点击明确确认；登录返回或打开分享页都不自动加入。
      const id = joined.value ? book.value!.id : await store.acceptInvitation(token.value, name.value.trim())
      if (id) { forgetInvitation(); store.selectBook(id); uni.redirectTo({ url: `/pages/shared-book/index?id=${encodeURIComponent(id)}` }) }
    } finally { joining.value = false }
    return
  }
  joining.value = true
  const id = isExample.value ? store.openFamilyExample(true) : book.value?.id
  if (!id) { joining.value = false; return }
  store.selectBook(id)
  uni.redirectTo({ url: `/pages/shared-book/index?id=${encodeURIComponent(id)}`, complete: () => { joining.value = false } })
}
function backToBooks() { forgetInvitation(); uni.redirectTo({ url: '/pages/shared-books/index' }) }
</script>

<template>
  <view class="page page-plain invite-page">
    <TopBar title="账本邀请" subtitle="共同记账，先从一个邀请开始。" back />
    <CloudStatus v-if="isCloudInvitation(token)" />
    <view v-if="loading" class="panel invite-preview">正在核对邀请…</view>
    <view v-else-if="valid" class="invite-preview panel">
      <view class="invite-from"><MemberAvatar :name="inviter.name" :color="inviter.color" :size="90" /><text>{{ inviter.name }}邀请你</text></view>
      <text class="invitation-title">一起记好小家的日常</text>
      <view class="invited-book"><AppIcon name="home" :size="57" /><text>{{ title }}</text><text class="preview-member-count">多人账本 · 确认加入后才可查看收支</text></view>
      <view class="invite-benefit"><AppIcon name="check" :size="26" /><text>共同查看收支、统计和日历</text></view><view class="invite-benefit"><AppIcon name="check" :size="26" /><text>每一笔保留记账人和修改记录</text></view><view class="invite-benefit"><AppIcon name="check" :size="26" /><text>成员维护自己的账单，创建者管理账本</text></view>
      <view v-if="store.accountId && isCloudInvitation(token) && !joined" class="join-name"><text>你在这个账本中的称呼</text><input v-model="name" :maxlength="12" placeholder="例如：小林、爸爸" /></view>
      <text class="invite-expiry">{{ isExample ? '示例邀请，接受后会在本机创建演示账本。' : preview ? `邀请有效至 ${localDate(new Date(preview.expiresAt))}` : '' }}</text>
      <button class="primary-button accept-invite" :disabled="joining || auth.busy || (isCloudInvitation(token) && store.syncing)" :loading="joining" @tap="accept">{{ joined ? '已在账本中，查看账本' : isExample ? '接受示例邀请' : auth.isLoggedIn ? '确认加入这个账本' : '微信登录后加入' }}</button>
      <button class="decline-invite" @tap="backToBooks">{{ joined ? '返回多人账本' : '暂不加入，返回多人账本' }}</button>
    </view>
    <view v-else class="invalid-invitation panel"><AppIcon name="info" :size="62" /><text class="invalid-title">暂时无法打开这份邀请</text><text class="invalid-copy">{{ error || '邀请可能已过期、撤销或不属于当前环境，请联系创建者重新邀请。' }}</text><button v-if="isCloudInvitation(token)" class="secondary-button" @tap="load">重新读取</button><button class="secondary-button" @tap="backToBooks">返回多人账本</button></view>
    <view class="invitation-demo-note"><AppIcon name="info" :size="28" /><text>{{ isExample ? '当前为本地示例，不会加入真实账本。' : '打开邀请不会自动加入。登录并确认后，你将能查看这个共同账本中的全部收支。' }}</text></view>
  </view>
</template>

<style scoped lang="scss">
.invite-preview { padding: 38rpx 30rpx 20rpx; }
.invite-from { display: flex; align-items: center; flex-direction: column; gap: 15rpx; font-size: 25rpx; color: #718b7a; }
.invitation-title { display: block; text-align: center; font-size: 35rpx; font-weight: 600; margin-top: 24rpx; }
.invited-book { display: flex; flex-direction: column; align-items: center; text-align: center; background: #e9f1e7; border-radius: 24rpx; padding: 32rpx 18rpx; gap: 13rpx; margin: 30rpx 0; font-size: 29rpx; font-weight: 500; overflow-wrap: anywhere; }
.invited-book .preview-member-count { font-size: 22rpx; color: #7f9887; font-weight: 400; }
.invite-benefit { display: flex; align-items: flex-start; gap: 12rpx; color: #7c9283; margin-top: 20rpx; font-size: 23rpx; }
.invite-expiry { display: block; text-align: center; font-size: 21rpx; color: #99a69c; margin: 32rpx 0 22rpx; }
.accept-invite { font-size: 26rpx; }
.decline-invite { font-size: 23rpx; color: #8b9c90; padding: 24rpx 0 0; }
.invitation-demo-note { display: flex; align-items: flex-start; gap: 10rpx; margin: 26rpx 8rpx; color: #8e9d92; font-size: 22rpx; line-height: 1.9; }
.invalid-invitation { display: flex; flex-direction: column; align-items: center; padding: 55rpx 32rpx; text-align: center; gap: 25rpx; }
.invalid-title { font-size: 32rpx; }
.invalid-copy { font-size: 24rpx; color: #8a9d90; line-height: 1.8; }
.invalid-invitation .secondary-button { width: 100%; font-size: 26rpx; margin-top: 15rpx; }
</style>

<style scoped lang="scss">
.join-name { margin: 28rpx 0; font-size: 25rpx; color: #718b7a; }
.join-name input { margin-top: 15rpx; padding: 20rpx; border: 1rpx solid #dce6de; border-radius: 16rpx; color: #285e4e; }
</style>
