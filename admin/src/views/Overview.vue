<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Refresh, ArrowRight, User, Notebook, Tickets } from '@element-plus/icons-vue'
import { api } from '../api'
import { datetime, number, errorMessage } from '../format'
import type { Overview } from '../types'

const data = ref<Overview>(), loading = ref(false), error = ref('')
async function load() {
  if (loading.value) return
  loading.value = true; error.value = ''; data.value = undefined
  try { data.value = await api<Overview>('/overview') }
  catch (e) { error.value = errorMessage(e) }
  finally { loading.value = false }
}
onMounted(load)
const metrics = computed(() => data.value ? [
  { label: '累计用户', value: data.value.users, extra: `今日新增 ${number(data.value.new_users_today)} 人`, icon: User, unit: '人' },
  { label: '使用中账本', value: data.value.books, extra: `其中共享账本 ${number(data.value.shared_books)} 个`, icon: Notebook, unit: '个' },
  { label: '有效账单', value: data.value.entries, extra: `今日新增 ${number(data.value.new_entries_today)} 笔`, icon: Tickets, unit: '笔' },
] : [])
const chartMax = computed(() => Math.max(1, ...data.value?.trend.flatMap(d => [d.users, d.entries]) ?? []))
</script>
<template>
  <div class="page-heading"><div><h1>数据概览</h1><p>了解墨记的使用情况，从每一笔记录开始。</p></div><el-button :icon="Refresh" :loading="loading" @click="load">刷新数据</el-button></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon><el-button text @click="load">重新加载</el-button></el-alert>
  <el-skeleton v-if="loading" :rows="10" animated class="surface skeleton" />
  <template v-else-if="data">
    <div class="metric-strip"><section v-for="m in metrics" :key="m.label" class="metric"><div class="metric-label"><span>{{ m.label }}</span><el-icon><component :is="m.icon" /></el-icon></div><div class="metric-value">{{ number(m.value) }}<small>{{ m.unit }}</small></div><div class="metric-extra">{{ m.extra }}</div></section></div>
    <div class="overview-grid">
      <section class="surface trend-panel"><div class="section-heading"><div><h2>记录在发生</h2><p>最近 14 天新增用户与有效账单</p></div><div class="chart-legend"><span><i class="legend-users"></i>用户</span><span><i class="legend-entries"></i>账单</span></div></div>
        <div class="trend-chart" role="img" aria-label="最近十四天新增用户和有效账单柱状图，下方提供详细数据表"><div class="chart-axis"><span>{{ chartMax }}</span><span>{{ Math.round(chartMax / 2) }}</span><span>0</span></div><div class="chart-plot"><div v-for="(point, index) in data.trend" :key="point.date" class="chart-day" :title="`${point.date}：新增用户 ${point.users} 人，新增账单 ${point.entries} 笔`"><div class="bar-pair"><span class="bar users" :style="{ height: `${point.users / chartMax * 100}%` }"></span><span class="bar entries" :style="{ height: `${point.entries / chartMax * 100}%` }"></span></div><span class="chart-date">{{ index % 2 === 0 || index === 13 ? point.date.slice(5).replace('-', '/') : '' }}</span></div></div></div>
        <details class="chart-details"><summary>查看每日数据</summary><el-table :data="data.trend" size="small"><el-table-column prop="date" label="日期" /><el-table-column prop="users" label="新增用户" /><el-table-column prop="entries" label="新增有效账单" /></el-table></details>
      </section>
      <aside class="surface activity-panel"><span class="activity-icon"><el-icon :size="24"><User /></el-icon></span><h2>最近 7 天登录用户</h2><div class="active-value">{{ number(data.active_users_7d) }}<small>人</small></div><p>按最近登录时间统计，<br />用于观察近期用户回访。</p><RouterLink to="/users" class="panel-link">查看用户<el-icon><ArrowRight /></el-icon></RouterLink><div class="activity-note">统计范围覆盖平台全部用户</div></aside>
    </div>
    <section class="surface quick-access"><div><h2>从这里开始查询</h2><p>按用户 ID 定位账号，或查看账本与成员状态。</p></div><div class="quick-actions"><RouterLink to="/users"><el-icon><User /></el-icon>用户查询<el-icon><ArrowRight /></el-icon></RouterLink><RouterLink to="/books"><el-icon><Notebook /></el-icon>账本查询<el-icon><ArrowRight /></el-icon></RouterLink></div></section>
    <p class="data-note">更新于 {{ datetime(data.generated_at) }}。新增账单按创建时间统计，已删除账单不计入；账本总数仅统计使用中的账本。</p>
  </template>
</template>
