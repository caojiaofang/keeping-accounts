<script setup lang="ts">
// 微信端去掉额外的组件宿主节点，让图标直接参与父容器的 flex 对齐。
defineOptions({ options: { virtualHost: true } })
withDefaults(defineProps<{ name: string; size?: number; light?: boolean }>(), { size: 40, light: false })
</script>

<template>
  <image :src="`/static/icons/${name}.svg`" mode="aspectFit" class="app-icon" :class="{ 'icon-light': light }" :style="{ width: `${size}rpx`, height: `${size}rpx` }" />
</template>

<style scoped>
.app-icon { display: block; flex-shrink: 0; }
.icon-light { filter: brightness(0) invert(1); }
</style>
