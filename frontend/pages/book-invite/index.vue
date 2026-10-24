<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad, onShow } from '@dcloudio/uni-app'
import TopBar from '@/components/TopBar.vue'
import AppIcon from '@/components/AppIcon.vue'
import MemberAvatar from '@/components/MemberAvatar.vue'
import { useLedgerStore } from '@/store/ledger'
import { activeMembers, DEMO_USER_ID, FAMILY_INVITATION, familyPartner, validInvitation } from '@/common/services/shared-books'
import { localDate, toast } from '@/common/utils/format'

const store = useLedgerStore()
store.hydrate()
const token = ref('')
const joining = ref(false)
const now = ref(Date.now())
const isExample = computed(() => token.value === FAMILY_INVITATION)
const invitation = computed(() => store.invitations.find(item => item.token === token.value))
const book = computed(() => isExample.value ? store.books.find(item => item.id === 'shared-joined-example') : store.books.find(item => item.id === invitation.value?.bookId))
const valid = computed(() => isExample.value || (!!invitation.value && validInvitation(invitation.value, book.value, now.value)))
const joined = computed(() => activeMembers(book.value).some(member => member.userId === DEMO_USER_ID))
const inviter = computed(() => isExample.value ? familyPartner : book.value?.members?.find(member => member.role === 'owner'))
onLoad(options => { token.value = options?.token || '' })
onShow(() => { now.value = Date.now() })
function accept() {
  if (joining.value) return
  now.value = Date.now()
  if (!valid.value) return toast('邀请已失效，请联系创建者重新邀请')
  if (!isExample.value && !joined.value) return toast('当前仅支持邀请预览，请体验示例邀请')
  joining.value = true
  const id = isExample.value ? store.openFamilyExample(true) : book.value!.id
  store.selectBook(id)
  uni.redirectTo({ url: `/pages/shared-book/index?id=${encodeURIComponent(id)}`, complete: () => { joining.value = false } })
}
function backToBooks() { uni.redirectTo({ url: '/pages/shared-books/index' }) }
</script>

<template>
  <view class="page page-plain invite-page">
    <TopBar title="账本邀请" subtitle="共同记账，先从一个邀请开始。" back />
    <view v-if="valid" class="invite-preview panel">
      <view class="invite-from"><MemberAvatar :name="inviter?.name || '创建者'" :color="inviter?.color" :size="90" /><text>{{ inviter?.name || '创建者' }}邀请你</text></view>
      <text class="invitation-title">一起记好小家的日常</text>
      <view class="invited-book"><AppIcon name="home" :size="57" /><text>{{ book?.name || '受邀家庭账本（示例）' }}</text><text class="preview-member-count">{{ activeMembers(book).length || 1 }} 位成员 · 多人账本</text></view>
      <view class="invite-benefit"><AppIcon name="check" :size="26" /><text>共同查看收支、统计和日历</text></view><view class="invite-benefit"><AppIcon name="check" :size="26" /><text>每一笔保留记账人，清楚又安心</text></view><view class="invite-benefit"><AppIcon name="check" :size="26" /><text>成员维护自己的账单，创建者管理账本</text></view>
      <text class="invite-expiry">{{ isExample ? '示例邀请，接受后会在本机创建演示账本。' : invitation ? `演示邀请有效至 ${localDate(new Date(invitation.expiresAt))}` : '' }}</text>
      <button class="primary-button accept-invite" :disabled="joining" @tap="accept">{{ joined ? '已在账本中，查看账本' : isExample ? '接受示例邀请' : '预览加入流程' }}</button>
      <button class="decline-invite" @tap="backToBooks">{{ joined ? '返回多人账本' : '暂不加入，返回多人账本' }}</button>
    </view>
    <view v-else class="invalid-invitation panel"><AppIcon name="info" :size="62" /><text class="invalid-title">这份邀请已失效</text><text class="invalid-copy">邀请可能已过期或撤销，也可能不属于本机。请联系账本创建者重新邀请。</text><button class="secondary-button" @tap="backToBooks">返回多人账本</button></view>
    <view class="invitation-demo-note"><AppIcon name="info" :size="28" /><text>当前为本地演示，页面中的家人与邀请均为示例。正式版需微信登录后，由后端验证邀请并同步共同账单。</text></view>
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
