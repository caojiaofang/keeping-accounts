<script setup lang="ts">
withDefaults(defineProps<{ name: string; size?: number; light?: boolean }>(), { size: 40, light: false })
</script>

<template>
  <image :src="`/static/icons/${name}.svg`" mode="aspectFit" class="app-icon" :class="{ 'icon-light': light }" :style="{ width: `${size}rpx`, height: `${size}rpx` }" />
</template>

<style scoped>
.app-icon { display: inline-block; flex-shrink: 0; vertical-align: middle; }
.icon-light { filter: brightness(0) invert(1); }
</style>
