import type { Book, Entry } from '@/common/types'
import { localDate } from '@/common/utils/format'

export const initialBooks: Book[] = [
  { id: 'daily', name: '日常账本', description: '认真生活的每一天', icon: 'leaf', color: '#e1eee7', budget: 500000 },
  { id: 'travel', name: '旅行手记', description: '把风景和快乐一起收藏', icon: 'plane', color: '#e7eef7', budget: 300000 },
  { id: 'home', name: '小家账本', description: '一起经营的小日子', icon: 'home', color: '#f4e9dd', budget: 800000 },
]

// 以首次打开当天为锚点生成演示记录，之后只读取本地保存的数据。
export function createDemoEntries(): Entry[] {
  const now = new Date()
  const year = now.getFullYear()
  const month = now.getMonth()
  const day = now.getDate()
  const entries: Entry[] = []
  const add = (offset: number, categoryId: string, amount: number, note: string, account = '微信', bookId = 'daily', type: 'expense' | 'income' = 'expense') => {
    const date = new Date(year, month, day - offset)
    entries.push({ id: `demo-${entries.length}`, bookId, categoryId, type, amount, note, account, date: localDate(date), createdAt: date.getTime() + entries.length })
  }
  add(0, 'food', 3200, '好好吃一顿午饭')
  add(0, 'coffee', 1800, '一杯拿铁，开启下午', '支付宝')
  add(0, 'transport', 600, '地铁通勤')
  add(1, 'shopping', 16900, '给自己买一件秋天的衣服', '支付宝')
  add(1, 'food', 4600, '和朋友的小聚')
  add(1, 'daily', 3590, '补充一点生活必需品')
  add(2, 'food', 2850, '工作日午餐')
  add(2, 'study', 6800, '一本想读很久的书')
  add(3, 'play', 5800, '周末电影', '支付宝')
  add(4, 'food', 4200, '晚餐')
  add(5, 'transport', 2400, '打车回家')
  add(6, 'health', 4800, '常备药品')
  add(7, 'house', 180000, '本月房租', '银行卡')
  add(8, 'salary', 1200000, '这个月的努力有了回响', '银行卡', 'daily', 'income')
  add(10, 'food', 5600, '周末早午餐')
  add(12, 'daily', 12800, '生活用品', '支付宝')
  add(2, 'travel', 39800, '周末出行车票', '支付宝', 'travel')
  add(3, 'food', 12800, '旅途中的当地味道', '微信', 'travel')
  add(1, 'daily', 8600, '给小家添一点绿意', '微信', 'home')
  return entries
}
