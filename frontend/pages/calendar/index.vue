<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import TopBar from '@/components/TopBar.vue'
import AppIcon from '@/components/AppIcon.vue'
import MonthPicker from '@/components/MonthPicker.vue'
import BookSwitcher from '@/components/BookSwitcher.vue'
import EntryList from '@/components/EntryList.vue'
import { useLedgerStore } from '@/store/ledger'
import { localDate, money, shiftMonth } from '@/common/utils/format'
import { buildExpenseCalendar, calendarAmount, filterCalendarEntries, isCalendarMonth, selectedDateInMonth } from '@/common/services/expense-calendar'
import type { CalendarMode, ExpenseCalendarDay } from '@/common/services/expense-calendar'

const store = useLedgerStore()
store.hydrate()
if (!isCalendarMonth(store.selectedMonth)) store.selectedMonth = localDate().slice(0, 7)
const today = ref(localDate())
const selectedDate = ref(selectedDateInMonth(store.selectedMonth, today.value, today.value))
const weekdays = ['一', '二', '三', '四', '五', '六', '日']
const mode = ref<CalendarMode>('all')
const modes: { key: CalendarMode; label: string }[] = [{ key: 'all', label: '全部' }, { key: 'expense', label: '支出' }, { key: 'income', label: '收入' }]
const showExpense = computed(() => mode.value !== 'income')
const showIncome = computed(() => mode.value !== 'expense')
const flowLabel = computed(() => mode.value === 'income' ? '收入' : '支出')
const calendar = computed(() => buildExpenseCalendar(store.selectedMonth, store.entries, store.activeBookId, today.value))
const selectedDay = computed(() => calendar.value.days.find(day => day.date === selectedDate.value)!)
const dayEntries = computed(() => filterCalendarEntries(store.entries, store.activeBookId, selectedDate.value, mode.value))
const peakDay = computed(() => mode.value === 'income' ? calendar.value.peakIncomeDay : calendar.value.peakDay)
const peakAmount = computed(() => mode.value === 'income' ? peakDay.value?.income || 0 : peakDay.value?.expense || 0)
const activeDays = computed(() => mode.value === 'income' ? calendar.value.incomeDays : calendar.value.expenseDays)
const emptyTitle = computed(() => mode.value === 'all' ? '这一天还没有账单' : `这一天还没有${flowLabel.value}记录`)
const dateTitle = computed(() => `${Number(selectedDate.value.slice(5, 7))}月${Number(selectedDate.value.slice(8))}日`)
const canGoPrevious = computed(() => isCalendarMonth(shiftMonth(store.selectedMonth, -1)))
const canGoNext = computed(() => isCalendarMonth(shiftMonth(store.selectedMonth, 1)))
watch(() => store.selectedMonth, month => { selectedDate.value = selectedDateInMonth(month, selectedDate.value, today.value) })

function stepMonth(delta: number) {
  const month = shiftMonth(store.selectedMonth, delta)
  if (isCalendarMonth(month)) store.selectedMonth = month
}
function selectDay(day: ExpenseCalendarDay) { if (day.inMonth) selectedDate.value = day.date }
function backToToday() { today.value = localDate(); store.selectedMonth = today.value.slice(0, 7); selectedDate.value = today.value }
function addRecord() { uni.navigateTo({ url: `/pages/record/index?date=${selectedDate.value}&type=${mode.value === 'income' ? 'income' : 'expense'}` }) }
function dayLabel(day: ExpenseCalendarDay) {
  const labels = [day.date]
  if (showExpense.value) labels.push(day.expenseCount ? `支出${money(day.expense)}元` : '未记支出')
  if (showIncome.value) labels.push(day.incomeCount ? `收入${money(day.income)}元` : '未记收入')
  return labels.join('，')
}
function dayClass(day: ExpenseCalendarDay) {
  if (mode.value === 'expense') return `heat-${day.level}`
  if (mode.value === 'income') return `income-heat-${day.incomeLevel}`
  return day.expenseCount || day.incomeCount ? 'has-records' : ''
}
</script>

<template>
  <view class="page calendar-page">
    <TopBar title="消费日历" subtitle="一格一天，收支都看得见。" back />
    <view class="calendar-context"><view class="month-controls"><button class="month-step" aria-label="上个月" :disabled="!canGoPrevious" @tap="stepMonth(-1)"><AppIcon name="back" :size="28" /></button><MonthPicker v-model="store.selectedMonth" /><button class="month-step" aria-label="下个月" :disabled="!canGoNext" @tap="stepMonth(1)"><AppIcon name="chevron" :size="28" /></button></view><BookSwitcher /></view>
    <view class="calendar-mode-switch"><button v-for="item in modes" :key="item.key" class="calendar-mode" :class="{ active: mode === item.key }" :aria-label="`显示${item.label}`" :aria-pressed="mode === item.key" @tap="mode = item.key">{{ item.label }}</button></view>
    <view class="calendar-panel panel">
      <view class="calendar-summary"><text class="calendar-caption">当月已记{{ mode === 'all' ? '收支' : flowLabel }}</text><button class="today-button" @tap="backToToday"><AppIcon name="calendar" :size="24" />回到今天</button></view>
      <view class="month-totals"><view v-if="showExpense" class="month-total"><text class="month-total-label">支出</text><text class="calendar-total expense-total number"><text>¥ </text>{{ money(calendar.expense) }}</text></view><view v-if="showIncome" class="month-total"><text class="month-total-label">收入</text><text class="calendar-total income-total number"><text>¥ </text>{{ money(calendar.income) }}</text></view></view>
      <view class="weekdays"><text v-for="(weekday, index) in weekdays" :key="weekday" :class="{ weekend: index > 4 }">{{ weekday }}</text></view>
      <view class="calendar-grid">
        <view v-for="(day, index) in calendar.days" :key="day.date" class="day-slot">
          <button v-if="day.inMonth" class="calendar-day" :class="[dayClass(day), { selected: day.date === selectedDate, 'is-today': day.isToday, 'show-both': mode === 'all' }]" :aria-label="dayLabel(day)" :aria-pressed="day.date === selectedDate" @tap="selectDay(day)"><view class="day-top"><text class="day-number number" :class="{ weekend: index % 7 > 4 }">{{ day.isToday ? '今' : day.day }}</text></view><text v-if="showExpense" class="day-amount expense-amount number" :class="{ recorded: day.expenseCount, 'large-amount': calendarAmount(day.expense).length > 6 }">{{ day.expenseCount ? `−${calendarAmount(day.expense)}` : '—' }}</text><text v-if="showIncome" class="day-amount income-amount number" :class="{ recorded: day.incomeCount, 'large-amount': calendarAmount(day.income).length > 6 }">{{ day.incomeCount ? `+${calendarAmount(day.income)}` : '—' }}</text></button>
          <view v-else class="outside-day" :class="{ 'show-both': mode === 'all' }"><text class="number">{{ day.day }}</text></view>
        </view>
      </view>
      <view class="calendar-legend"><view v-if="mode === 'all'" class="flow-legend"><text class="expense-legend-label">− 支出</text><text class="income-legend-label">+ 收入</text><text>每天分别显示两项金额</text></view><view v-else class="flow-legend"><text>{{ flowLabel }}</text><view v-for="level in [1, 2, 3]" :key="level" class="legend-level" :class="mode === 'income' ? `income-heat-${level}` : `heat-${level}`" /><text>由少到多</text></view></view>
      <view v-if="mode === 'all'" class="month-insight"><text>已记支出 <text class="insight-number number">{{ calendar.expenseDays }}</text> 天</text><text>已记收入 <text class="insight-number number">{{ calendar.incomeDays }}</text> 天</text></view>
      <view v-else class="month-insight"><text>已记{{ flowLabel }} <text class="insight-number number">{{ activeDays }}</text> 天</text><text v-if="peakDay">最多的一天：{{ peakDay.day }}日 <text class="insight-number number">¥{{ money(peakAmount) }}</text></text><text v-else>本月还没有{{ flowLabel }}记录</text></view>
    </view>
    <text class="calendar-help">日格金额单位为元，大额以“万 / 亿”缩写；— 表示该项没有记录。</text>
    <view class="day-section">
      <view class="day-section-heading"><view class="row"><text class="section-title">{{ dateTitle }}</text><text v-if="selectedDate === today" class="selected-today">今天</text></view><text class="day-entry-count">{{ dayEntries.length }} 笔账单</text></view>
      <view class="day-summary"><view v-if="showExpense"><text>支出</text><text class="number">¥{{ money(selectedDay.expense) }}</text></view><view v-if="showIncome"><text>收入</text><text class="number day-income">¥{{ money(selectedDay.income) }}</text></view></view>
      <EntryList v-if="dayEntries.length" :entries="dayEntries" :show-date-headings="false" />
      <view v-else class="day-empty panel"><view class="empty-calendar"><AppIcon name="calendar" :size="48" /></view><text class="day-empty-title">{{ emptyTitle }}</text><text class="day-empty-copy">{{ mode === 'all' ? '没有记录，不代表没有收支。想起来时，再补一笔。' : '可切换「全部」查看当天记录，也可以补记一笔。' }}</text></view>
    </view>
    <view class="calendar-footer"><button class="primary-button" @tap="addRecord"><AppIcon name="plus" :size="32" light />为{{ dateTitle }}{{ mode === 'income' ? '记收入' : '记一笔' }}</button></view>
  </view>
</template>

<style scoped lang="scss">
.calendar-page { padding-bottom: calc(160rpx + env(safe-area-inset-bottom)); }
.calendar-context { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24rpx; gap: 12rpx; }
.month-controls { display: flex; align-items: center; gap: 10rpx; }
.month-step { display: flex; align-items: center; padding: 8rpx 0; }
.month-step[disabled] { background: transparent; opacity: .25; }
.calendar-mode-switch { display: flex; gap: 8rpx; background: #e8efea; padding: 7rpx; border-radius: 20rpx; margin-bottom: 22rpx; }
.calendar-mode { flex: 1; color: #8a9c8f; text-align: center; padding: 14rpx; border-radius: 15rpx; font-size: 25rpx; }
.calendar-mode.active { background: #fff; color: #285e4e; font-weight: 600; }
.calendar-panel { padding: 26rpx 16rpx 0; }
.calendar-summary { display: flex; align-items: center; justify-content: space-between; padding: 0 12rpx 18rpx; }
.calendar-caption { font-size: 22rpx; color: #8a9b8f; }
.month-totals { display: flex; gap: 24rpx; padding: 0 12rpx 25rpx; }
.month-total { flex: 1; min-width: 0; }
.month-total-label { font-size: 20rpx; color: #97a598; }
.calendar-total { display: block; font-size: 36rpx; font-weight: 500; line-height: 1.3; margin-top: 6rpx; overflow-wrap: anywhere; }
.calendar-total > text { font-size: 23rpx; }
.income-total { color: #ab7f49; }
.today-button { display: flex; align-items: center; gap: 8rpx; font-size: 21rpx; color: #688974; background: #edf4ed; padding: 11rpx 16rpx; border-radius: 13rpx; }
.weekdays { display: flex; border-top: 1rpx solid #edf2ed; padding: 20rpx 0 14rpx; }
.weekdays > text { width: 14.285714%; text-align: center; font-size: 22rpx; color: #8f9f93; }
.weekdays > text.weekend { color: #b29b7d; }
.calendar-grid { display: flex; flex-wrap: wrap; }
.day-slot { width: 14.285714%; padding: 4rpx; }
.calendar-day { display: flex; flex-direction: column; align-items: center; justify-content: center; width: 100%; height: 92rpx; border: 2rpx solid transparent; border-radius: 15rpx; }
.heat-1 { background: #f0f5ef; }
.heat-2 { background: #e3eee1; }
.heat-3 { background: #cddfc8; }
.income-heat-1 { background: #faf5ec; }
.income-heat-2 { background: #f3e7d3; }
.income-heat-3 { background: #e8d2af; }
.calendar-day.has-records { background: #f1f5ef; }
.calendar-day.show-both { height: 122rpx; }
.calendar-day.selected { border-color: #3c7052; }
.day-top { position: relative; display: flex; justify-content: center; }
.day-number { display: flex; align-items: center; justify-content: center; width: 38rpx; height: 38rpx; font-size: 25rpx; line-height: 1; color: #58735e; border-radius: 50%; }
.day-number.weekend { color: #9e8b70; }
.is-today .day-number { box-shadow: inset 0 0 0 1rpx #749b7c; font-size: 21rpx; }
.selected .day-number { background: #285e4e; color: #fff; box-shadow: none; }
.day-amount { font-size: 20rpx; margin-top: 5rpx; line-height: 1.2; color: #bdc8be; letter-spacing: -.3rpx; white-space: nowrap; }
.expense-amount.recorded { color: #55734e; }
.income-amount.recorded { color: #ad7d42; }
.show-both .day-amount { font-size: 20rpx; letter-spacing: -.5rpx; }
.day-amount.large-amount { font-size: 16rpx; }
.selected .day-amount { font-weight: 600; }
.outside-day { display: flex; align-items: center; justify-content: center; height: 92rpx; padding-bottom: 23rpx; color: #d5dcd5; font-size: 25rpx; }
.outside-day.show-both { height: 122rpx; padding-bottom: 58rpx; }
.calendar-legend { display: flex; justify-content: space-between; padding: 20rpx 12rpx 25rpx; color: #a1aea1; font-size: 18rpx; }
.flow-legend { display: flex; align-items: center; gap: 10rpx; flex-wrap: wrap; }
.expense-legend-label { color: #6c8c6e; margin-right: 15rpx; }
.income-legend-label { color: #ad824e; margin-right: 15rpx; }
.legend-level { width: 17rpx; height: 17rpx; border-radius: 4rpx; }
.month-insight { display: flex; align-items: center; justify-content: space-between; gap: 12rpx; border-top: 1rpx solid #eaf0e9; padding: 23rpx 12rpx; color: #9aa998; font-size: 19rpx; }
.insight-number { color: #6d886d; }
.calendar-help { display: block; margin: 18rpx 4rpx 0; font-size: 19rpx; line-height: 1.7; color: #a0aca2; }
.day-section { margin-top: 32rpx; }
.day-section-heading { display: flex; align-items: center; justify-content: space-between; }
.day-entry-count { font-size: 22rpx; color: #97a599; }
.selected-today { margin-left: 15rpx; background: #e4eee5; color: #749179; font-size: 19rpx; padding: 4rpx 10rpx; border-radius: 7rpx; }
.day-summary { display: flex; gap: 35rpx; margin: 15rpx 0 23rpx; }
.day-summary > view { display: flex; align-items: baseline; gap: 12rpx; font-size: 22rpx; color: #95a297; }
.day-summary .number { font-size: 26rpx; color: #58725f; }
.day-summary .day-income { color: #aa875b; }
.day-empty { display: flex; flex-direction: column; align-items: center; padding: 40rpx 25rpx; }
.empty-calendar { display: flex; align-items: center; justify-content: center; background: #edf3eb; width: 88rpx; height: 88rpx; border-radius: 26rpx; }
.day-empty-title { font-size: 27rpx; color: #6b836e; margin-top: 20rpx; }
.day-empty-copy { font-size: 21rpx; color: #a0ada1; margin-top: 12rpx; text-align: center; }
.calendar-footer { position: fixed; bottom: 0; left: 0; right: 0; background: #f6f8f7; border-top: 1rpx solid #e6ece6; padding: 18rpx 36rpx calc(20rpx + env(safe-area-inset-bottom)); z-index: 25; }
.calendar-footer .primary-button { height: 88rpx; font-size: 27rpx; }
/* #ifdef H5 */
@media (min-width: 700px) { .calendar-footer { max-width: 480px; left: 50%; transform: translateX(-50%); } }
/* #endif */
</style>
