<script setup lang="ts">
import { ref, watch } from 'vue'
import { onLoad, onShow } from '@dcloudio/uni-app'
import AppIcon from '@/components/AppIcon.vue'
import { safeLoginRedirect } from '@/common/services/auth'
import { useAuthStore } from '@/store/auth'
import { pendingInvitationPath } from '@/common/services/invitations'

const auth = useAuthStore()
const destination = ref('/pages/ledger/index')
const statusHeight = uni.getSystemInfoSync().statusBarHeight || 24
let leaving = false

function finish() {
  if (leaving) return
  leaving = true
  uni.reLaunch({ url: destination.value, fail() { leaving = false } })
}

onLoad(options => { destination.value = safeLoginRedirect(options?.redirect || pendingInvitationPath()) })
onShow(() => {
  leaving = false
  if (auth.user) finish()
  else void auth.bootstrap()
})
watch(() => auth.user, value => { if (value && !auth.loggingIn) finish() })

async function login() {
  if (auth.busy) return
  await auth.login()
  if (auth.user) { finish(); uni.showToast({ title: '登录成功', icon: 'success' }) }
}

function guest() { auth.enterGuest(); if (!auth.busy) finish() }
function explain() {
  uni.showModal({
    title: '关于微信登录',
    content: '墨记通过微信标识识别你的账号并保存登录状态，不获取头像、昵称或手机号。\n\n登录后的个人账本和分析对话保存在云端，体验数据和升级前的本机记录不会自动上传。智能记账会发送本轮文字、主动选择的图片、最近对话及待确认草稿给模型服务；图片会移除元数据，不在墨记服务端保存。记账对话只在当前页面保留，需点击卡片确认后才保存账单。消费分析会按需发送统计或必要明细给模型服务。',
    showCancel: false,
    confirmText: '知道了',
    confirmColor: '#285e4e',
  })
}
</script>

<template>
  <view class="login-page" :style="{ paddingTop: `${statusHeight + 20}px` }">
    <view class="login-top"><view class="small-brand"><AppIcon name="leaf" :size="30" /><text>墨记</text></view><text class="top-note">认真生活的每一天</text></view>
    <view class="login-story">
      <view class="notebook" aria-hidden="true">
        <view class="notebook-spine" />
        <view class="notebook-page"><view class="notebook-mark"><AppIcon name="leaf" :size="65" /></view><view class="notebook-title">日子有迹可循</view><view class="paper-rule" /><view class="paper-line"><view class="paper-dot" /><view /><view /></view><view class="paper-line"><view class="paper-dot pale" /><view /><view /></view><view class="paper-line short"><view class="paper-dot" /><view /><view /></view><view class="paper-bottom">生活里的小事，值得被记住。</view></view>
      </view>
      <text class="login-brand">墨记</text>
      <text class="login-title">把日子，一笔一笔记好。</text>
      <view class="login-copy"><text>从一顿早餐，到一个小小的心愿，</text><text>让每一次认真生活，都有记录。</text></view>
    </view>
    <view class="login-actions">
      <view v-if="auth.error" class="login-error" role="alert"><AppIcon name="info" :size="29" /><text>{{ auth.error }}</text></view>
      <button class="wechat-login-button" :disabled="auth.busy" :loading="auth.loggingIn || auth.restoring" @tap="login"><text>{{ auth.restoring ? '正在恢复登录' : auth.loggingIn ? '正在微信登录' : auth.error ? '重新微信登录' : '微信登录' }}</text></button>
      <button class="guest-entry" :disabled="auth.busy" @tap="guest">先体验一下</button>
      <view class="login-privacy"><AppIcon name="shield" :size="24" /><text>仅用于识别账号，不获取头像、昵称或手机号</text></view>
      <button class="login-explain" @tap="explain">登录说明</button>
    </view>
  </view>
</template>

<style scoped lang="scss">
.login-page { min-height: 100vh; display: flex; flex-direction: column; padding: 0 52rpx calc(32rpx + env(safe-area-inset-bottom)); background: #f6f8f7; }
.login-top { display: flex; align-items: center; justify-content: space-between; padding-right: 135rpx; min-height: 52rpx; }
.small-brand { display: flex; align-items: center; gap: 9rpx; color: #507461; font-size: 25rpx; letter-spacing: 4rpx; white-space: nowrap; }
.top-note { color: #8b9c91; font-size: 19rpx; white-space: nowrap; }
.login-story { display: flex; flex: 1; flex-direction: column; align-items: center; justify-content: center; padding: 54rpx 0 48rpx; }
.notebook { position: relative; width: 326rpx; height: 350rpx; margin: 0 0 48rpx; border-radius: 12rpx 28rpx 28rpx 12rpx; background: #d6e6da; padding: 12rpx 10rpx 12rpx 22rpx; transform: rotate(-6deg); box-shadow: 14rpx 20rpx 0 #e8eee9; }
.notebook-spine { position: absolute; left: 13rpx; top: 0; bottom: 0; width: 2rpx; background: #b9cfc0; }
.notebook-page { height: 100%; padding: 24rpx 28rpx; border-radius: 5rpx 19rpx 19rpx 5rpx; background: #fcfdfb; }
.notebook-mark { display: flex; justify-content: center; opacity: .8; }
.notebook-title { text-align: center; color: #4d715b; font-size: 22rpx; letter-spacing: 3rpx; margin: 8rpx 0 22rpx; }
.paper-rule { height: 1rpx; background: #dce7de; margin-bottom: 20rpx; }
.paper-line { display: flex; align-items: center; gap: 12rpx; margin-bottom: 19rpx; }
.paper-dot { width: 15rpx; height: 15rpx; flex-shrink: 0; border-radius: 5rpx; background: #acc8b3; }
.paper-dot.pale { background: #d0dfd0; }
.paper-line > view:nth-child(2) { height: 7rpx; width: 94rpx; background: #e3ebe3; border-radius: 4rpx; }
.paper-line > view:nth-child(3) { height: 7rpx; width: 36rpx; margin-left: auto; background: #c4d8ca; border-radius: 4rpx; }
.paper-line.short > view:nth-child(2) { width: 65rpx; }
.paper-bottom { margin-top: 24rpx; text-align: center; color: #97aa99; font-size: 15rpx; white-space: nowrap; }
.login-brand { font-size: 60rpx; letter-spacing: 12rpx; font-weight: 600; padding-left: 12rpx; line-height: 1.3; }
.login-title { font-size: 29rpx; letter-spacing: 1rpx; margin-top: 17rpx; }
.login-copy { color: #7f9387; font-size: 24rpx; line-height: 1.85; text-align: center; margin-top: 22rpx; }
.login-copy text { display: block; }
.login-actions { padding: 0 0 9rpx; }
button.wechat-login-button { display: flex; align-items: center; justify-content: center; width: 100%; height: 100rpx; border-radius: 25rpx; color: #fff; background: #285e4e; font-size: 30rpx; font-weight: 600; letter-spacing: 2rpx; }
button.wechat-login-button[disabled] { background: #aec3b6; color: #fff; }
button.guest-entry { width: 100%; color: #668270; font-size: 26rpx; padding: 25rpx 0; }
button.guest-entry[disabled] { background: transparent; color: #a5b4aa; }
.login-privacy { display: flex; align-items: center; justify-content: center; gap: 6rpx; color: #8a9e90; font-size: 18rpx; margin-top: 10rpx; }
button.login-explain { color: #64846f; font-size: 20rpx; margin: 12rpx auto 0; padding: 8rpx 18rpx; }
.login-error { display: flex; align-items: flex-start; gap: 12rpx; background: #f5ece5; color: #8e5f44; font-size: 23rpx; padding: 20rpx; border-radius: 18rpx; margin-bottom: 20rpx; }
.wechat-login-button:focus-visible, .guest-entry:focus-visible, .login-explain:focus-visible { outline: 4rpx solid #7ca08a; outline-offset: 5rpx; }
@media (max-height: 680px) { .login-story { padding: 32rpx 0; } .notebook { width: 284rpx; height: 300rpx; margin-bottom: 35rpx; } .notebook-page { padding: 18rpx 24rpx; } .notebook-title { margin-bottom: 15rpx; } .paper-line { margin-bottom: 14rpx; } .paper-bottom { margin-top: 16rpx; font-size: 12rpx; } .login-brand { font-size: 52rpx; } .login-copy { margin-top: 16rpx; } }
</style>
