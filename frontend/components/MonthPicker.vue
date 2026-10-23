<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import { monthLabel } from '@/common/format'
defineProps<{ modelValue: string; compact?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
function change(event: { detail: { value: string } }) {
  emit('update:modelValue', event.detail.value.slice(0, 7))
}
</script>

<template>
  <picker mode="date" fields="month" :value="modelValue" start="2000-01" end="2100-12" @change="change">
    <view class="month-picker" :class="{ compact }"><text>{{ monthLabel(modelValue) }}</text><AppIcon name="down" :size="25" /></view>
  </picker>
</template>

<style scoped>
.month-picker { display: flex; align-items: center; gap: 12rpx; font-size: 27rpx; font-weight: 500; padding: 7rpx 0; }
.compact { font-size: 25rpx; }
</style>
