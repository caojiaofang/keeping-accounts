<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { Refresh, Search } from '@element-plus/icons-vue'
import { api, query } from '../api'
import { datetime, number, errorMessage } from '../format'
import type { AuditEvent, Page } from '../types'

const actions: Record<string,string> = { login:'登录', logout:'退出登录', password_change:'修改密码', logout_all:'退出全部会话', password_reset:'服务端重置密码', bootstrap:'账号初始化' }
const reasons: Record<string,string> = { invalid_credentials:'账号或密码不正确', rate_limited:'请求过于频繁', current_password_invalid:'原密码不正确', same_password:'新旧密码相同', account_changed:'账号状态已更新', service_unavailable:'服务暂时不可用' }
const action=ref(''), outcome=ref(''), page=ref(1), pageSize=ref(20), data=ref<Page<AuditEvent>>(), loading=ref(false), error=ref('')
let filters={ action:'',outcome:'' }, controller: AbortController | undefined
async function load() {
  controller?.abort(); controller=new AbortController(); const current=controller
  loading.value=true; error.value=''; data.value=undefined
  try { data.value=await api<Page<AuditEvent>>(`/account/logs${query({...filters,page:page.value,page_size:pageSize.value})}`,{signal:AbortSignal.any([current.signal,AbortSignal.timeout(20000)])}) }
  catch(e) { if(!current.signal.aborted) error.value=errorMessage(e) }
  finally { if(controller===current) loading.value=false }
}
function search() { filters={action:action.value,outcome:outcome.value}; page.value=1; void load() }
function reset() { action.value=''; outcome.value=''; search() }
onMounted(load); onBeforeUnmount(()=>controller?.abort())
</script>
<template>
  <div class="page-heading"><div><h1>登录日志</h1><p>查看管理员登录、退出、改密与会话撤销记录。</p></div><el-tag effect="plain" type="info">只读记录</el-tag></div>
  <section class="surface table-panel"><form class="filter-bar" @submit.prevent="search"><div class="filter-field filter-small"><label for="log-action">事件类型</label><el-select id="log-action" v-model="action" clearable placeholder="全部事件"><el-option v-for="(label,value) in actions" :key="value" :label="label" :value="value" /></el-select></div><div class="filter-field filter-small"><label for="log-outcome">操作结果</label><el-select id="log-outcome" v-model="outcome" clearable placeholder="全部结果"><el-option label="成功" value="success" /><el-option label="失败" value="failure" /></el-select></div><el-button type="primary" :icon="Search" native-type="submit">查询</el-button><el-button :icon="Refresh" @click="reset">重置</el-button></form>
    <div class="table-heading"><h2>事件记录 <span v-if="data">{{ number(data.total) }}</span></h2><el-button text :icon="Refresh" :loading="loading" @click="load">刷新</el-button></div><el-alert v-if="error" :title="error" type="error" :closable="false" show-icon><el-button text @click="load">重新加载</el-button></el-alert>
    <el-table v-loading="loading" :data="data?.items ?? []" row-key="id" empty-text="暂无符合条件的事件记录，请调整筛选条件。"><el-table-column label="时间" min-width="170"><template #default="{row}">{{ datetime(row.created_at) }}</template></el-table-column><el-table-column prop="username" label="账号" min-width="110" show-overflow-tooltip /><el-table-column label="事件" min-width="140"><template #default="{row}">{{ actions[row.action] ?? row.action }}</template></el-table-column><el-table-column label="结果" width="100"><template #default="{row}"><el-tag :type="row.outcome === 'success' ? 'success' : 'danger'" effect="light">{{ row.outcome === 'success' ? '成功' : '失败' }}</el-tag></template></el-table-column><el-table-column label="说明" min-width="180"><template #default="{row}">{{ reasons[row.reason] ?? (row.reason || '—') }}</template></el-table-column><el-table-column prop="ip" label="来源 IP" min-width="160" show-overflow-tooltip /><el-table-column prop="user_agent" label="浏览器 / 设备" min-width="250" show-overflow-tooltip /></el-table>
    <div class="pagination"><span>按发生时间从新到旧排列，时间为北京时间</span><el-pagination v-model:current-page="page" v-model:page-size="pageSize" :page-sizes="[20,50,100]" :total="data?.total ?? 0" layout="total, sizes, prev, pager, next" @current-change="load" @size-change="page=1; load()" /></div>
  </section>
</template>
