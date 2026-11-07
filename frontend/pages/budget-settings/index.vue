<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import TopBar from '@/components/TopBar.vue'
import CloudStatus from '@/components/CloudStatus.vue'
import { useLedgerStore } from '@/store/ledger'
import { useBudgetStore } from '@/store/budget'
import { canWriteBook } from '@/common/services/shared-books'
import { fromBudgetForm, toBudgetForm, newBudgetItem } from '@/common/services/budget-editor'
import { previousBudgetMonth } from '@/common/services/monthly-budget'
import { localDate, money, toast } from '@/common/utils/format'
import { newId } from '@/common/utils/id'

const ledger = useLedgerStore(), budget = useBudgetStore()
ledger.hydrate()
const form = ref(toBudgetForm(null)), ready = ref(false), dirty = ref(false), busy = ref(false), message = ref('')
const editable = computed(() => canWriteBook(ledger.activeBook) && ledger.selectedMonth >= localDate().slice(0, 7))
const locked = computed(() => !editable.value || !ready.value || busy.value || budget.writing || budget.retryAvailable)
const total = computed(() => form.value.items.filter(i => i.kind === 'expense').reduce((n, i) => n + (Number(i.amount) || 0), 0))
let generation = 0
function fill() {
  form.value = toBudgetForm(budget.month?.plan || null)
  form.value.income ||= '0'
  if (!form.value.groups.length) form.value.groups.push({ id: newId(), name: '本月预算' })
  dirty.value = false
}
function touch() { dirty.value = true; message.value = '' }
async function reload() {
  const current = ++generation
  ready.value = false; busy.value = true; message.value = ''
  try {
    if (ledger.accountId && !await ledger.refreshCloud()) { message.value = ledger.syncError || '请连接云端账本后重试'; return }
    if (current !== generation || !await budget.load() || current !== generation) return
    fill(); ready.value = true
  } finally { if (current === generation) busy.value = false }
}
onShow(() => { if (!dirty.value && !budget.retryAvailable) void reload() })
watch(() => [ledger.accountId, ledger.activeBookId, ledger.selectedMonth], () => { generation++; dirty.value = false; void reload() }, { flush: 'post' })
async function discard() {
  if (!dirty.value) return true
  return new Promise<boolean>(resolve => uni.showModal({ title: '放弃未保存的修改？', content: '切换后将重新读取所选月份的预算。', confirmText: '放弃修改', success: r => resolve(r.confirm) }))
}
async function selectMonth(value: string) { if (!busy.value && !budget.writing && !budget.retryAvailable && await discard()) ledger.selectedMonth = value }
async function selectBook(event: { detail: { value: string | number } }) { if (!busy.value && !budget.writing && !budget.retryAvailable && await discard()) ledger.selectBook(ledger.books[Number(event.detail.value)].id) }
async function copyPrevious() {
  if (locked.value) return
  const previous = previousBudgetMonth(ledger.selectedMonth)
  if (!previous) return toast('没有可沿用的上一个月')
  if (dirty.value && !await discard()) return
  busy.value = true; message.value = ''; const current = generation
  try {
    const value = await budget.query('monthly', previous)
    if (current !== generation || !value) return
    if (!value.plan) { message.value = `${previous} 尚未设置预算，可以手动添加本月项目。`; return }
    form.value = toBudgetForm(value.plan); touch()
    message.value = `已填入 ${previous} 的预算项目和额度，保存后生效；本月消费单独累计。`
  } finally { if (current === generation) busy.value = false }
}
function add() {
  if (locked.value) return
  if (form.value.items.length >= 200) return toast('最多 200 个项目')
  form.value.items.push(newBudgetItem(form.value.groups[0].id, localDate())); touch()
}
function remove(id: string) {
  if (locked.value) return
  const current = generation
  uni.showModal({ title: '删除此预算项目？', content: '保存本月预算后生效。相关消费会转为未关联预算，原账单和其他月份的预算保留。', confirmText: '删除项目', confirmColor: '#9b4c38', success: r => {
    if (r.confirm && !locked.value && current === generation) { form.value.items = form.value.items.filter(i => i.id !== id); touch() }
  } })
}
async function deleteMonth() {
  if (locked.value || budget.month?.state !== 'confirmed') return
  const current = generation, month = ledger.selectedMonth, version = budget.month.version
  const confirmed = await new Promise<boolean>(resolve => uni.showModal({ title: `删除 ${month} 的预算？`, content: '本月所有预算项目将删除，相关消费转为未关联预算，原账单和其他月份不受影响。未保存的修改也会放弃。', confirmText: '删除本月预算', confirmColor: '#9b4c38', success: r => resolve(r.confirm) }))
  if (!confirmed || locked.value || current !== generation) return
  busy.value = true; message.value = ''
  try {
    if (await budget.write('deleteMonthly', month, { requestId: newId(), version }) && current === generation) { dirty.value = false; toast('本月预算已删除'); if (!budget.refreshError) fill() }
  } finally { if (current === generation) busy.value = false }
}
async function save() {
  if (locked.value) return
  message.value = ''
  try {
    const plan = fromBudgetForm(form.value)
    busy.value = true; const current = generation
    const result = await budget.write('saveMonthly', ledger.selectedMonth, { requestId: newId(), version: budget.month?.version || 0, plan })
    if (result && current === generation) { dirty.value = false; toast('本月预算已保存'); if (!budget.refreshError) fill() }
  } catch (cause) { message.value = cause instanceof Error ? cause.message : '请检查预算内容' }
  finally { busy.value = false }
}
async function retry() {
  if (busy.value) return
  busy.value = true; const current = generation, deleting = budget.pendingMethod === 'deleteMonthly'
  try { if (await budget.retry() && current === generation) { dirty.value = false; toast(deleting ? '本月预算已删除' : '本月预算已保存'); if (!budget.refreshError) { fill(); ready.value = true } } }
  finally { if (current === generation) busy.value = false }
}
async function reset() { if (!busy.value && !budget.writing && await discard()) await reload() }
</script>

<template>
  <view class="page monthly-editor">
    <TopBar title="设置月预算" subtitle="这个月，每一项准备花多少。" back />
    <CloudStatus />
    <view class="context-row">
      <picker :range="ledger.books" range-key="name" :disabled="busy || budget.writing || budget.retryAvailable" @change="selectBook"><text>{{ ledger.activeBook.name }} ▾</text></picker>
      <picker mode="date" fields="month" :value="ledger.selectedMonth" start="2000-01" end="2100-12" :disabled="busy || budget.writing || budget.retryAvailable" @change="selectMonth($event.detail.value)"><text>{{ ledger.selectedMonth }} ▾</text></picker>
    </view>
    <text class="hint">{{ ledger.activeBook.kind === 'shared' ? '所有有效家庭成员均可创建、修改和删除本月预算。' : '预算按月保存，可以新增、修改或删除项目。' }}</text>
    <text v-if="!ledger.accountId" class="hint">当前为本地体验，登录后不会自动上传。</text>
    <text v-if="!editable" class="hint">{{ ledger.selectedMonth < localDate().slice(0, 7) ? '历史月份保留当时预算，仅可查看。' : '只有有效账本成员可以修改预算。' }}</text>
    <view v-if="message || budget.error || budget.refreshError" class="message" role="alert">{{ message || budget.error || budget.refreshError }}</view>
    <view v-if="budget.retryAvailable" class="message"><text>上次保存结果尚未确认，请重试原请求。</text><button :disabled="busy || budget.writing" @tap="retry">重试保存</button></view>
    <button v-else-if="budget.error || budget.refreshError" class="text-button" :disabled="busy || budget.writing" @tap="reset">重新读取预算</button>
    <text v-if="!ready" class="hint">正在读取预算…</text>
    <view v-if="ready">
      <button class="copy-button" :disabled="locked" @tap="copyPrevious">沿用上月预算</button>
      <view class="budget-total"><text>本月支出预算合计</text><text>¥{{ money(Math.round(total * 100)) }}</text></view>
      <view class="columns"><text>预算项目</text><text>本月额度（元）</text></view>
      <view v-for="item in form.items" :key="item.id" class="budget-row">
        <view class="item-fields"><input v-model="item.name" placeholder="例如：餐饮、交通、房贷" maxlength="24" :disabled="locked" aria-label="预算项目名称" @input="touch" /><input v-model="item.amount" class="amount" type="digit" placeholder="0.00" maxlength="10" :disabled="locked" :aria-label="`${item.name || '项目'}本月额度`" @input="touch" /></view>
        <view class="row-foot"><text class="hint">{{ item.kind === 'expense' ? '记账时可选择此项目' : '原有预留或储蓄项目，保留原记录' }}</text><button :disabled="locked" :aria-label="`删除${item.name || '项目'}`" @tap="remove(item.id)">删除</button></view>
      </view>
      <text v-if="!form.items.length" class="empty">还没有预算项目，添加一项开始安排本月花费。</text>
      <button class="add-button" :disabled="locked" @tap="add">＋ 添加预算项目</button>
      <text class="hint">本次修改只影响 {{ ledger.selectedMonth }}。下个月可选择沿用，也可以重新设置。</text>
      <button class="primary" :disabled="locked" :loading="budget.writing" @tap="save">保存本月预算</button>
      <button v-if="budget.month?.state === 'confirmed' && editable" class="delete-month" :disabled="locked" @tap="deleteMonth">删除本月全部预算</button>
    </view>
  </view>
</template>

<style scoped lang="scss">
.monthly-editor { padding-bottom: calc(50rpx + env(safe-area-inset-bottom)); }
.context-row,.columns,.item-fields,.row-foot { display: flex; align-items: center; justify-content: space-between; gap: 22rpx; }
.context-row { margin: 12rpx 0 24rpx; font-size: 26rpx; }
.hint { display: block; font-size: 23rpx; color: #71877a; line-height: 1.8; margin: 14rpx 0; }
.copy-button,.add-button { color: #285e4e; font-size: 27rpx; padding: 24rpx 0; text-align: left; }
.copy-button { border-bottom: 1rpx solid #dce5df; }
.budget-total { padding: 30rpx 0; }.budget-total text { display: block; font-size: 25rpx; }.budget-total text:last-child { font-size: 42rpx; margin-top: 12rpx; }
.columns { font-size: 23rpx; color: #71877a; padding: 18rpx 0; }
.budget-row { padding: 24rpx 0 10rpx; border-top: 1rpx solid #dce5df; }.item-fields input { font-size: 29rpx; flex: 1; min-width: 0; height: 64rpx; }.item-fields .amount { flex: 0 0 210rpx; text-align: right; color: #285e4e; }
.row-foot .hint { margin: 4rpx 0; }.row-foot button { font-size: 23rpx; color: #9b4c38; padding: 12rpx 0 12rpx 24rpx; }
.primary { color: #fff; background: #285e4e; padding: 26rpx; border-radius: 14rpx; font-size: 28rpx; margin-top: 28rpx; }
.delete-month { margin-top: 22rpx; padding: 24rpx; color: #9b4c38; font-size: 25rpx; }
.message { padding: 24rpx; background: #edf2ee; font-size: 25rpx; line-height: 1.8; border-radius: 12rpx; margin: 20rpx 0; }.message button,.text-button { color: #285e4e; padding: 20rpx 0; text-align: left; }.empty { display: block; font-size: 26rpx; color: #71877a; padding: 35rpx 0; }button[disabled] { opacity: .5; }
</style>
