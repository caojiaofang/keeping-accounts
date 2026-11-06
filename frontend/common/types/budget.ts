/** 家庭预算第一版契约。金额均为整数分，日期使用北京时间自然日。 */
import type { Entry } from './index'

export type BudgetMonth = string
export type BudgetItemKind = 'expense' | 'reserve' | 'saving'

export interface BudgetGroup { id: string; name: string }
export interface ReserveCycle {
  expectedAmount: number
  anchorDate: string
  cycleMonths: 1 | 6 | 12
}
export interface BudgetItem extends BudgetGroup {
  groupId: string
  kind: BudgetItemKind
  monthlyAmount: number
  dueDay: number | null
  reserve: ReserveCycle | null
  note: string
}
export interface BudgetPlan {
  plannedIncome: number
  groups: BudgetGroup[]
  items: BudgetItem[]
}
export interface BudgetRevision { id: string; effectiveMonth: BudgetMonth; plan: BudgetPlan }
export interface PreviewBudgetRevision {
  version: number
  effectiveMonth: BudgetMonth
  plan: BudgetPlan
}
export interface SaveBudgetRevision extends PreviewBudgetRevision {
  requestId: string
  impactToken: string
}
export interface BudgetItemAmount { itemId: string; amount: number }
export interface BudgetMonthOverride {
  plannedIncome: number | null
  itemAmounts: BudgetItemAmount[]
}
export interface ConfirmBudgetMonth {
  requestId: string
  sourceRevisionId: string
  planVersion: number
}
export interface UpdateBudgetMonth {
  requestId: string
  version: number
  override: BudgetMonthOverride
}
export interface BudgetGroupTotal { groupId: string; amount: number }
export interface BudgetTotals {
  plannedIncome: number
  expenseAmount: number
  reserveAmount: number
  savingAmount: number
  totalAllocated: number
  unallocated: number
  groupTotals: BudgetGroupTotal[]
}
export interface BudgetPlanResponse {
  schemaVersion: 1
  bookId: string
  version: number
  revisions: BudgetRevision[]
}
export interface BudgetImpactResponse {
  impactToken: string
  expiresAt: number
  version: number
  effectiveMonth: BudgetMonth
  untilMonth: BudgetMonth | null
  confirmedMonths: BudgetMonth[]
  overriddenMonths: BudgetMonth[]
  totals: BudgetTotals
}
export interface SavedBudgetRevision {
  schemaVersion: 1
  bookId: string
  version: number
  revision: BudgetRevision
}
interface BudgetMonthBase {
  schemaVersion: 1
  bookId: string
  month: BudgetMonth
  planVersion: number
  override: BudgetMonthOverride
  generatedAt: number
}
export type BudgetMonthResponse = BudgetMonthBase & (
  | { state: 'not_set'; snapshotId: null; version: 0; sourceRevisionId: null; plan: null; totals: null }
  | { state: 'preview'; snapshotId: null; version: 0; sourceRevisionId: string; plan: BudgetPlan; totals: BudgetTotals }
  | { state: 'confirmed'; snapshotId: string; version: number; sourceRevisionId: string; plan: BudgetPlan; totals: BudgetTotals }
)
export interface BudgetMonthAmount { month: BudgetMonth; amount: number }
export interface PreviewBudgetEqualization {
  version: number
  itemId: string
  totalAmount: number
  startMonth: BudgetMonth
  endMonth: BudgetMonth
}
export interface ApplyBudgetEqualization extends PreviewBudgetEqualization {
  requestId: string
  impactToken: string
}
export interface BudgetEqualizationPreview {
  impactToken: string
  expiresAt: number
  schedule: BudgetMonthAmount[]
  affectedMonths: BudgetMonth[]
}
export interface BudgetEqualizationResponse {
  schemaVersion: 1
  months: BudgetMonthResponse[]
}
/** 测算输入；availableAmount 由服务端查询实际资金，不能据客户端值写入储备。 */
export interface BudgetTarget {
  startDate: string
  targetDate: string
  targetAmount: number
  availableAmount: number
  usedAmount: number
}
export interface BudgetFundingPreview {
  fundedAmount: number
  gapAmount: number
  surplusAmount: number
  remainingMonths: number
  overdue: boolean
  additionalSchedule: BudgetMonthAmount[]
}
export interface BudgetFundMutation { requestId: string; fundVersion: number }
export interface CreateBudgetFund extends BudgetFundMutation {
  id: string
  kind: 'opening' | 'deposit'
  amount: number
  date: string
  note?: string
}
export interface BudgetFundReplacement { id: string; amount: number; date: string; note?: string }
export interface CorrectBudgetFund extends BudgetFundMutation {
  recordVersion: number
  replacement: BudgetFundReplacement | null
}
export interface BudgetGoalTerms {
  startDate: string
  targetDate: string
  targetAmount: number
  carryInAmount: number
}
export interface CreateBudgetGoal extends BudgetFundMutation, BudgetGoalTerms { id: string }
export interface UpdateBudgetGoal extends BudgetFundMutation, BudgetGoalTerms {
  version: number
  state: 'active' | 'closed'
}
export interface BudgetFundSummary {
  asOfDate: string
  openingAmount: number
  depositedAmount: number
  usedAmount: number
  availableAmount: number
}
export interface BudgetFundRecordResponse {
  id: string
  itemId: string
  kind: 'opening' | 'deposit' | 'use'
  amount: number
  date: string
  note: string
  entryId: string | null
  goalPeriodId: string | null
  replacesId: string | null
  version: number
  voidedAt: number | null
  createdAt: number
  updatedAt: number
  createdBy: string
  updatedBy: string
}
export interface BudgetGoalResponse extends BudgetGoalTerms {
  id: string
  itemId: string
  state: 'active' | 'closed'
  version: number
  closedAt: number | null
  collectedAmount: number
  usedAmount: number
  availableAmount: number
  forecast: BudgetFundingPreview
}
export interface BudgetProjectResponse {
  id: string
  kind: BudgetItemKind
  status: 'active' | 'archived'
  enabledMonth: BudgetMonth
  fundVersion: number
  configuration: BudgetItem
}
export interface BudgetProjectPage { schemaVersion: 1; bookId: string; items: BudgetProjectResponse[]; nextCursor: string | null }
export interface BudgetItemDetail {
  schemaVersion: 1
  bookId: string
  generatedAt: number
  item: BudgetProjectResponse
  balance: BudgetFundSummary
  goals: BudgetGoalResponse[]
  records: BudgetFundRecordResponse[]
  nextRecordCursor: string | null
}
export interface SavedBudgetFund {
  schemaVersion: 1
  bookId: string
  itemId: string
  fundVersion: number
  record: BudgetFundRecordResponse
  replacement: BudgetFundRecordResponse | null
  balance: BudgetFundSummary
}
export interface SavedBudgetGoal {
  schemaVersion: 1
  bookId: string
  itemId: string
  fundVersion: number
  goal: BudgetGoalResponse
  balance: BudgetFundSummary
}

export interface BudgetLinkMutation { requestId: string; entryVersion: number; linkVersion: number }
export interface SetBudgetEntryLink extends BudgetLinkMutation { itemId: string; goalPeriodId?: string | null }
export interface RemoveBudgetEntryLink extends BudgetLinkMutation {}
export interface BudgetEntryLinkResponse {
  entryId: string
  bookId: string
  itemId: string
  goalPeriodId: string | null
  version: number
  isDeleted: boolean
  updatedAt: number
  updatedBy: string
}
export interface BudgetProjectVersion { itemId: string; fundVersion: number }
export interface BudgetEntryLinkState {
  schemaVersion: 1
  bookId: string
  entryId: string
  entryVersion: number
  linkVersion: number
  link: BudgetEntryLinkResponse | null
  fundVersions: BudgetProjectVersion[]
  notice: string | null
}
export interface BudgetLinkedEntry { entry: Entry; link: BudgetEntryLinkResponse | null; linkVersion: number }
export interface BudgetEntryPage { schemaVersion: 1; bookId: string; entries: BudgetLinkedEntry[]; nextCursor: string | null }
export interface BudgetExecutionDetail extends BudgetItemDetail { entries: BudgetLinkedEntry[]; nextEntryCursor: string | null }
export interface BudgetItemExecution {
  itemId: string
  kind: BudgetItemKind
  name: string
  groupId: string
  inPlan: boolean
  plannedAmount: number
  expenseAmount: number
  remainingAmount: number | null
  depositedAmount: number
  usedAmount: number
  availableAmount: number | null
  fundVersion: number
}
export interface BudgetMonthExecution {
  schemaVersion: 1
  bookId: string
  month: BudgetMonth
  generatedAt: number
  budget: BudgetMonthResponse
  expenseTotal: number
  linkedExpenseTotal: number
  unlinkedExpenseTotal: number
  items: BudgetItemExecution[]
}
