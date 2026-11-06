<script setup lang="ts">
import { ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
const props = withDefaults(defineProps<{ open: boolean; title: string; description?: string; placeholder?: string; initial?: string; numeric?: boolean; maxlength?: number; loading?: boolean }>(), { description: '', placeholder: '', initial: '', numeric: false, maxlength: 24, loading: false })
const emit = defineEmits<{ close: []; confirm: [value: string] }>()
const value = ref('')
watch(() => props.open, open => { if (open) value.value = props.initial })
</script>

<template>
  <view v-if="open" class="sheet-mask" @tap="!loading && emit('close')">
    <view class="sheet" @tap.stop>
      <view class="sheet-handle" />
      <view class="between"><text class="sheet-title">{{ title }}</text><button class="icon-button" aria-label="关闭" :disabled="loading" @tap="emit('close')"><AppIcon name="close" :size="34" /></button></view>
      <text v-if="description" class="sheet-description">{{ description }}</text>
      <input v-model="value" class="sheet-input" :disabled="loading" :type="numeric ? 'digit' : 'text'" :placeholder="placeholder" :maxlength="maxlength" :adjust-position="true" />
      <button class="primary-button" :disabled="loading" :loading="loading" @tap="!loading && emit('confirm', value.trim())">{{ loading ? '保存中' : '保存' }}</button>
    </view>
  </view>
</template>
