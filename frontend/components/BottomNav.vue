<script setup lang="ts">
import AppIcon from './AppIcon.vue'
defineProps<{ active: string }>()
const tabs = [
  { key: 'ledger', name: '明细', icon: 'list' },
  { key: 'statistics', name: '统计', icon: 'chart' },
  { key: 'add', name: '记一笔', icon: 'plus' },
  { key: 'books', name: '账本', icon: 'book' },
  { key: 'profile', name: '我的', icon: 'user' },
]
function navigate(key: string) {
  if (key === 'add') uni.navigateTo({ url: '/pages/record/index' })
  else {
    const pages = getCurrentPages()
    if (!pages[pages.length - 1]?.route?.includes(`pages/${key}/`)) uni.redirectTo({ url: `/pages/${key}/index` })
  }
}
</script>

<template>
  <view class="bottom-nav">
    <button v-for="tab in tabs" :key="tab.key" class="nav-item" :class="{ selected: active === tab.key, 'nav-add': tab.key === 'add' }" :aria-label="tab.name" @tap="navigate(tab.key)">
      <view class="nav-icon" :class="{ 'add-icon': tab.key === 'add' }"><AppIcon :name="tab.icon" :size="tab.key === 'add' ? 45 : 42" :light="tab.key === 'add'" /></view>
      <text class="nav-label">{{ tab.name }}</text>
      <view v-if="active === tab.key" class="active-dot" />
    </button>
  </view>
</template>

<style scoped lang="scss">
.bottom-nav { position: fixed; left: 0; right: 0; bottom: 0; background: #fff; border-top: 1rpx solid #eaf0ec; display: flex; justify-content: space-around; height: calc(132rpx + env(safe-area-inset-bottom)); padding: 18rpx 18rpx env(safe-area-inset-bottom); z-index: 30; }
.nav-item { flex: 1; display: flex; align-items: center; flex-direction: column; position: relative; color: #87958b; }
.nav-icon { height: 45rpx; opacity: .42; display: flex; align-items: center; justify-content: center; }
.nav-label { font-size: 21rpx; margin-top: 8rpx; }
.selected { color: #285e4e; font-weight: 600; }
.selected .nav-icon { opacity: 1; }
.active-dot { width: 6rpx; height: 6rpx; background: #285e4e; border-radius: 50%; margin-top: 5rpx; }
.nav-add { overflow: visible; }
.add-icon { width: 88rpx; height: 88rpx; border-radius: 30rpx; background: #285e4e; margin-top: -39rpx; opacity: 1; box-shadow: 0 8rpx 20rpx #285e4e20; border: 6rpx solid #fff; }
.nav-add .nav-label { margin-top: 5rpx; color: #3f6654; }
</style>
