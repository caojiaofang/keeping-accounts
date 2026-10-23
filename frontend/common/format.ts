export function money(cents: number, decimals = 2): string {
  const [integer, fraction] = (Math.abs(cents) / 100).toFixed(decimals).split('.')
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return fraction === undefined ? grouped : `${grouped}.${fraction}`
}

export function localDate(date = new Date()): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

export function monthLabel(month: string): string {
  const [year, value] = month.split('-')
  return `${year}年${Number(value)}月`
}

export function dateLabel(value: string): string {
  const today = localDate()
  const yesterday = new Date()
  yesterday.setDate(yesterday.getDate() - 1)
  const date = new Date(`${value}T12:00:00`)
  const prefix = value === today ? '今天' : value === localDate(yesterday) ? '昨天' : `${date.getMonth() + 1}月${date.getDate()}日`
  return `${prefix}  周${'日一二三四五六'[date.getDay()]}`
}

export function shiftMonth(month: string, delta: number): string {
  const [year, value] = month.split('-').map(Number)
  return localDate(new Date(year, value - 1 + delta, 1)).slice(0, 7)
}

export function toast(title: string): void {
  uni.showToast({ title, icon: 'none' })
}
