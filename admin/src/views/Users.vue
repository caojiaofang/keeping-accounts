<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Search, Refresh } from '@element-plus/icons-vue'
import { api, query } from '../api'
import { datetime, number, errorMessage } from '../format'
import IdText from '../components/IdText.vue'
import type { Page, User } from '../types'

const router = useRouter(), q = ref(''), appliedQ = ref(''), page = ref(1), pageSize = ref(20)
const data = ref<Page<User>>(), loading = ref(false), error = ref('')
const drawer = ref(false), selected = ref<User>(), detailLoading = ref(false), detailError = ref('')
let controller: AbortController | undefined, detailController: AbortController | undefined
async function load() {
  controller?.abort(); controller = new AbortController()
  const current = controller
  loading.value = true; error.value = ''; data.value = undefined
  try { data.value = await api<Page<User>>(`/users${query({ q: appliedQ.value, page: page.value, page_size: pageSize.value })}`, { signal: AbortSignal.any([current.signal, AbortSignal.timeout(20000)]) }) }
  catch (e) { if (!current.signal.aborted) error.value = errorMessage(e) }
  finally { if (controller === current) loading.value = false }
}
function search() { appliedQ.value = q.value.trim(); page.value = 1; void load() }
function reset() { q.value = ''; search() }
async function detail(id: string) {
  detailController?.abort(); detailController = new AbortController()
  const current = detailController
  drawer.value = true; detailLoading.value = true; detailError.value = ''; selected.value = undefined
  try { selected.value = await api<User>(`/users/${id}`, { signal: AbortSignal.any([current.signal, AbortSignal.timeout(20000)]) }) }
  catch (e) { if (!current.signal.aborted) detailError.value = errorMessage(e) }
  finally { if (detailController === current) detailLoading.value = false }
}
onMounted(load)
onBeforeUnmount(() => { controller?.abort(); detailController?.abort() })
</script>
<template>
  <div class="page-heading"><div><h1>用户查询</h1><p>了解用户的注册、登录与记账情况。</p></div><el-tag effect="plain" type="info">只读查询</el-tag></div>
  <section class="surface table-panel"><form class="filter-bar" @submit.prevent="search"><div class="filter-field"><label for="user-search">用户 ID</label><el-input id="user-search" v-model="q" placeholder="输入完整或部分用户 ID" clearable :prefix-icon="Search" maxlength="100" /></div><el-button type="primary" :icon="Search" native-type="submit">查询</el-button><el-button :icon="Refresh" @click="reset">重置</el-button></form>
    <div class="table-heading"><h2>用户列表 <span v-if="data">{{ number(data.total) }}</span></h2><el-button text :icon="Refresh" :loading="loading" @click="load">刷新</el-button></div>
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false"><el-button text @click="load">重新加载</el-button></el-alert>
    <el-table v-loading="loading" :data="data?.items ?? []" row-key="id" empty-text="没有符合条件的用户，请调整用户 ID 后重试。"><el-table-column label="用户 ID" min-width="330"><template #default="{ row }"><IdText :value="row.id" /></template></el-table-column><el-table-column label="注册时间" min-width="170"><template #default="{ row }">{{ datetime(row.created_at) }}</template></el-table-column><el-table-column label="最近登录" min-width="170"><template #default="{ row }">{{ datetime(row.last_login_at) }}</template></el-table-column><el-table-column prop="book_count" label="持有账本" min-width="90" align="right" /><el-table-column prop="entry_count" label="创建账单" min-width="90" align="right" /><el-table-column label="操作" width="90" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="detail(row.id)">查看</el-button></template></el-table-column></el-table>
    <div class="pagination"><span>按注册时间从新到旧排列</span><el-pagination v-model:current-page="page" v-model:page-size="pageSize" :page-sizes="[20, 50, 100]" :total="data?.total ?? 0" layout="total, sizes, prev, pager, next" @current-change="load" @size-change="page = 1; load()" /></div>
  </section><p class="data-note">持有账本指当前拥有且使用中的账本；创建账单不含已删除记录。</p>
  <el-drawer v-model="drawer" title="用户详情" size="min(560px, 100vw)" @closed="detailController?.abort()"><el-skeleton v-if="detailLoading" :rows="6" animated /><el-alert v-if="detailError" :title="detailError" type="error" :closable="false" /><template v-if="selected"><div class="detail-lead"><span class="detail-avatar">用</span><div><h2>墨记用户</h2><p>注册于 {{ datetime(selected.created_at) }}</p></div></div><dl class="detail-list"><dt>用户 ID</dt><dd><IdText :value="selected.id" /></dd><dt>最近登录</dt><dd>{{ datetime(selected.last_login_at) }}</dd><dt>使用中账本</dt><dd>{{ selected.book_count }} 个</dd><dt>创建有效账单</dt><dd>{{ selected.entry_count }} 笔</dd></dl><el-button type="primary" @click="router.push({ path: '/books', query: { owner_id: selected.id } })">查询该用户持有的账本</el-button></template></el-drawer>
</template>
