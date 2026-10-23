<script setup lang="ts">
import AppIcon from './AppIcon.vue'
withDefaults(defineProps<{ title?: string; subtitle?: string; brand?: boolean; back?: boolean }>(), { title: '', subtitle: '', brand: false, back: false })
const statusHeight = uni.getSystemInfoSync().statusBarHeight || 24
function goBack() {
  if (getCurrentPages().length > 1) uni.navigateBack()
  else uni.redirectTo({ url: '/pages/ledger/index' })
}
</script>

<template>
  <view class="top-bar" :style="{ paddingTop: `${statusHeight + 8}px` }">
    <view class="top-main">
      <button v-if="back" class="back-button" aria-label="返回" @tap="goBack"><AppIcon name="back" :size="42" /></button>
      <view v-if="brand" class="brand-symbol"><AppIcon name="leaf" :size="43" light /></view>
      <view class="top-titles">
        <text class="top-title" :class="{ 'brand-title': brand }">{{ title }}</text>
        <text v-if="subtitle" class="top-subtitle">{{ subtitle }}</text>
      </view>
      <view class="top-actions"><slot /></view>
    </view>
  </view>
</template>

<style scoped lang="scss">
.top-bar { padding-bottom: 32rpx; }
.top-main { min-height: 88rpx; display: flex; align-items: center; padding-right: 170rpx; position: relative; }
.brand-symbol { width: 65rpx; height: 65rpx; background: #285e4e; border-radius: 21rpx 21rpx 21rpx 7rpx; display: flex; justify-content: center; align-items: center; margin-right: 18rpx; }
.top-titles { display: flex; flex-direction: column; }
.top-title { font-size: 37rpx; font-weight: 600; letter-spacing: 2rpx; }
.brand-title { font-size: 41rpx; letter-spacing: 5rpx; }
.top-subtitle { font-size: 21rpx; color: #8c9991; margin-top: 3rpx; white-space: nowrap; }
.top-actions { margin-left: 12rpx; }
.back-button { width: 58rpx; height: 72rpx; margin-left: -16rpx; margin-right: 10rpx; display: flex; align-items: center; justify-content: center; }
</style>
