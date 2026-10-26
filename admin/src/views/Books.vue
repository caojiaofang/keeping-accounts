<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Search, Refresh } from '@element-plus/icons-vue'
import { api, query } from '../api'
import { datetime, number, errorMessage } from '../format'
import IdText from '../components/IdText.vue'
import type { Book, BookDetail, Page } from '../types'

const route = useRoute(), router = useRouter()
const q = ref(''), kind = ref(''), status = ref(''), ownerId = ref(''), page = ref(1), pageSize = ref(20)
let filters = { q: '', kind: '', status: '', owner_id: '' }
const data = ref<Page<Book>>(), loading = ref(false), error = ref('')
const drawer = ref(false), selected = ref<BookDetail>(), detailLoading = ref(false), detailError = ref('')
let controller: AbortController | undefined, detailController: AbortController | undefined
async function load() {
  controller?.abort(); controller = new AbortController()
  const current = controller
  loading.value = true; error.value = ''; data.value = undefined
  try { data.value = await api<Page<Book>>(`/books${query({ ...filters, page: page.value, page_size: pageSize.value })}`, { signal: AbortSignal.any([current.signal, AbortSignal.timeout(20000)]) }) }
  catch (e) { if (!current.signal.aborted) error.value = errorMessage(e) }
  finally { if (controller === current) loading.value = false }
}
function search() {
  if (ownerId.value.trim() && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(ownerId.value.trim())) {
    error.value = '持有人 ID 需要输入完整的用户 UUID'; return
  }
  filters = { q: q.value.trim(), kind: kind.value, status: status.value, owner_id: ownerId.value.trim() }; page.value = 1; void load()
}
function applyRoute() { ownerId.value = typeof route.query.owner_id === 'string' ? route.query.owner_id : ''; search() }
function reset() { q.value = ''; kind.value = ''; status.value = ''; ownerId.value = ''; if (route.query.owner_id) void router.replace('/books'); else search() }
async function detail(id: string) {
  detailController?.abort(); detailController = new AbortController()
  const current = detailController
  drawer.value = true; detailLoading.value = true; detailError.value = ''; selected.value = undefined
  try { selected.value = await api<BookDetail>(`/books/${id}`, { signal: AbortSignal.any([current.signal, AbortSignal.timeout(20000)]) }) }
  catch (e) { if (!current.signal.aborted) detailError.value = errorMessage(e) }
  finally { if (detailController === current) detailLoading.value = false }
}
onMounted(applyRoute)
watch(() => route.query.owner_id, applyRoute)
onBeforeUnmount(() => { controller?.abort(); detailController?.abort() })
const memberStatus: Record<string, string> = { active: '参与中', removed: '已移出', left: '已退出' }
</script>
<template>
  <div class="page-heading"><div><h1>账本查询</h1><p>查看个人与共享账本，定位成员和账本状态。</p></div><el-tag effect="plain" type="info">只读查询</el-tag></div>
  <section class="surface table-panel"><form class="filter-bar book-filters" @submit.prevent="search"><div class="filter-field"><label for="book-search">账本名称 / ID</label><el-input id="book-search" v-model="q" clearable placeholder="输入名称或部分 ID" maxlength="100" :prefix-icon="Search" /></div><div class="filter-field filter-small"><label for="book-kind">账本类型</label><el-select id="book-kind" v-model="kind" clearable placeholder="全部类型"><el-option label="个人账本" value="personal" /><el-option label="共享账本" value="shared" /></el-select></div><div class="filter-field filter-small"><label for="book-status">账本状态</label><el-select id="book-status" v-model="status" clearable placeholder="全部状态"><el-option label="使用中" value="active" /><el-option label="已解散" value="closed" /></el-select></div><div class="filter-field owner-filter"><label for="book-owner">持有人 ID</label><el-input id="book-owner" v-model="ownerId" clearable placeholder="完整用户 ID（可选）" maxlength="36" /></div><el-button type="primary" :icon="Search" native-type="submit">查询</el-button><el-button :icon="Refresh" @click="reset">重置</el-button></form>
    <div class="table-heading"><h2>账本列表 <span v-if="data">{{ number(data.total) }}</span></h2><el-button text :icon="Refresh" :loading="loading" @click="load">刷新</el-button></div><el-alert v-if="error" :title="error" type="error" :closable="false" show-icon><el-button text @click="search">重新加载</el-button></el-alert>
    <el-table v-loading="loading" :data="data?.items ?? []" row-key="id" empty-text="没有符合条件的账本，请调整筛选条件后重试。"><el-table-column label="账本" min-width="340"><template #default="{ row }"><div class="book-name">{{ row.name }}</div><IdText :value="row.id" /></template></el-table-column><el-table-column label="类型" width="110"><template #default="{ row }"><el-tag :type="row.kind === 'shared' ? 'primary' : 'info'" effect="light">{{ row.kind === 'shared' ? '共享账本' : '个人账本' }}</el-tag></template></el-table-column><el-table-column label="状态" width="100"><template #default="{ row }"><span class="status-dot" :class="row.status"></span>{{ row.status === 'active' ? '使用中' : '已解散' }}</template></el-table-column><el-table-column prop="entry_count" label="有效账单" width="100" align="right" /><el-table-column label="在册成员" width="100" align="right"><template #default="{ row }">{{ row.kind === 'personal' ? '—' : row.member_count }}</template></el-table-column><el-table-column label="创建时间" min-width="170"><template #default="{ row }">{{ datetime(row.created_at) }}</template></el-table-column><el-table-column label="操作" width="90" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="detail(row.id)">查看</el-button></template></el-table-column></el-table>
    <div class="pagination"><span>按创建时间从新到旧排列</span><el-pagination v-model:current-page="page" v-model:page-size="pageSize" :page-sizes="[20, 50, 100]" :total="data?.total ?? 0" layout="total, sizes, prev, pager, next" @current-change="load" @size-change="page = 1; load()" /></div>
  </section><p class="data-note">在册成员包含账本持有人；已解散账本保留历史信息，无法在此修改或删除。</p>
  <el-drawer v-model="drawer" title="账本详情" size="min(680px, 100vw)" @closed="detailController?.abort()"><el-skeleton v-if="detailLoading" :rows="7" animated /><el-alert v-if="detailError" :title="detailError" type="error" :closable="false" /><template v-if="selected"><div class="detail-lead"><span class="detail-avatar">账</span><div><h2>{{ selected.name }}</h2><p>{{ selected.kind === 'shared' ? '共享账本' : '个人账本' }} · {{ selected.status === 'active' ? '使用中' : '已解散' }}</p></div></div><dl class="detail-list"><dt>账本 ID</dt><dd><IdText :value="selected.id" /></dd><dt>持有人 ID</dt><dd><IdText :value="selected.owner_id" /></dd><dt>创建时间</dt><dd>{{ datetime(selected.created_at) }}</dd><dt>有效账单</dt><dd>{{ selected.entry_count }} 笔</dd></dl><template v-if="selected.kind === 'shared'"><h3>成员记录</h3><el-table :data="selected.members" empty-text="暂无成员记录"><el-table-column label="用户 ID" min-width="320"><template #default="{ row }"><IdText :value="row.user_id" /></template></el-table-column><el-table-column label="角色" width="90"><template #default="{ row }">{{ row.role === 'owner' ? '持有人' : '成员' }}</template></el-table-column><el-table-column label="状态" width="90"><template #default="{ row }">{{ memberStatus[row.status] ?? row.status }}</template></el-table-column><el-table-column label="加入时间" min-width="170"><template #default="{ row }">{{ datetime(row.joined_at) }}</template></el-table-column></el-table></template></template></el-drawer>
</template>
