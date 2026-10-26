<script setup lang="ts">
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { CopyDocument } from '@element-plus/icons-vue'
defineProps<{ value: string }>()
async function copy(value: string) {
  try { await navigator.clipboard.writeText(value); ElMessage.success('已复制完整 ID') }
  catch { ElMessage.warning('复制失败，请选择 ID 后手动复制') }
}
</script>
<template>
  <span class="id-text" :title="value"><code>{{ value }}</code><el-button text size="small" :icon="CopyDocument" :aria-label="`复制 ID ${value}`" @click="copy(value)" /></span>
</template>
