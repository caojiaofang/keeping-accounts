<script setup lang="ts">
import { getCurrentInstance, nextTick, onMounted, onBeforeUnmount, watch } from 'vue'
import { onReady } from '@dcloudio/uni-app'
const props = defineProps<{ segments: { value: number; color: string }[] }>()
const instance = getCurrentInstance()
const canvasId = `donut-${instance?.uid || 0}`
const size = 168
let ready = false
let nativeContext: CanvasRenderingContext2D | undefined
let retryTimer: ReturnType<typeof setTimeout> | undefined
let disposed = false
function paintNative(ctx: CanvasRenderingContext2D) {
  ctx.clearRect(0, 0, size, size)
  ctx.lineWidth = 19
  ctx.strokeStyle = '#edf2ee'
  ctx.beginPath()
  ctx.arc(size / 2, size / 2, 65, 0, Math.PI * 2)
  ctx.stroke()
  const total = props.segments.reduce((sum, segment) => sum + segment.value, 0)
  let start = -Math.PI / 2
  if (total) props.segments.forEach(segment => {
    const angle = segment.value / total * Math.PI * 2
    ctx.beginPath()
    ctx.strokeStyle = segment.color
    ctx.arc(size / 2, size / 2, 65, start + .018, start + Math.max(angle - .018, .018))
    ctx.stroke()
    start += angle
  })
}
function initialize(attempt = 0) {
  if (disposed) return
  ready = true
  // #ifdef MP-WEIXIN
  if (nativeContext) { paintNative(nativeContext); return }
  nextTick(() => {
    uni.createSelectorQuery().in(instance?.proxy).select(`#${canvasId}`).fields({ node: true, size: true }, () => {}).exec(results => {
      if (disposed) return
      const node = results[0]?.node
      if (!node) {
        if (attempt < 8) retryTimer = setTimeout(() => initialize(attempt + 1), 80)
        return
      }
      const ratio = uni.getSystemInfoSync().pixelRatio || 1
      node.width = size * ratio
      node.height = size * ratio
      const ctx = node.getContext('2d') as CanvasRenderingContext2D
      ctx.scale(ratio, ratio)
      nativeContext = ctx
      paintNative(ctx)
    })
  })
  // #endif
  // #ifndef MP-WEIXIN
  draw()
  // #endif
}
function draw() {
  if (!ready) return
  // #ifdef MP-WEIXIN
  if (nativeContext) paintNative(nativeContext)
  return
  // #endif
  // #ifndef MP-WEIXIN
  nextTick(() => {
    const ctx = uni.createCanvasContext(canvasId, instance?.proxy)
    ctx.clearRect(0, 0, size, size)
    const total = props.segments.reduce((sum, segment) => sum + segment.value, 0)
    ctx.setLineWidth(19)
    ctx.setStrokeStyle('#edf2ee')
    ctx.beginPath()
    ctx.arc(size / 2, size / 2, 65, 0, Math.PI * 2)
    ctx.stroke()
    let start = -Math.PI / 2
    if (total) props.segments.forEach(segment => {
      const angle = segment.value / total * Math.PI * 2
      ctx.beginPath()
      ctx.setStrokeStyle(segment.color)
      ctx.arc(size / 2, size / 2, 65, start + .018, start + Math.max(angle - .018, .018))
      ctx.stroke()
      start += angle
    })
    ctx.draw()
  })
  // #endif
}
onReady(() => initialize())
onMounted(() => initialize())
onBeforeUnmount(() => { disposed = true; if (retryTimer) clearTimeout(retryTimer) })
watch(() => props.segments, draw, { deep: true })
</script>

<template>
  <view class="donut">
    <!-- #ifdef MP-WEIXIN -->
    <canvas :id="canvasId" type="2d" :style="{ width: `${size}px`, height: `${size}px` }" />
    <!-- #endif -->
    <!-- #ifndef MP-WEIXIN -->
    <canvas :id="canvasId" :canvas-id="canvasId" :style="{ width: `${size}px`, height: `${size}px` }" />
    <!-- #endif -->
    <view class="donut-center"><slot /></view>
  </view>
</template>

<style scoped>
.donut { width: 168px; height: 168px; position: relative; margin: 0 auto; }
.donut-center { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; pointer-events: none; }
</style>
