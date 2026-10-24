<script setup lang="ts">
withDefaults(defineProps<{ name: string; color?: string; size?: number }>(), { color: '#dcece1', size: 72 })
</script>

<template>
  <view class="member-avatar" :style="{ width: `${size}rpx`, height: `${size}rpx`, background: color, fontSize: `${size * .38}rpx` }"><text>{{ name.slice(0, 1) }}</text></view>
</template>

<style scoped>
.member-avatar { display: flex; align-items: center; justify-content: center; flex-shrink: 0; border-radius: 50%; color: #456550; font-weight: 600; border: 4rpx solid #fff; }
</style>
