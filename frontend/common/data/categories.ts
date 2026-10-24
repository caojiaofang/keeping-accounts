import type { Category } from '@/common/types'

export const categories: Category[] = [
  { id: 'food', name: '餐饮', icon: 'food', color: '#fbefe1', type: 'expense' },
  { id: 'coffee', name: '咖啡茶饮', icon: 'coffee', color: '#eee9e3', type: 'expense' },
  { id: 'transport', name: '交通', icon: 'bus', color: '#e8eefb', type: 'expense' },
  { id: 'shopping', name: '购物', icon: 'bag', color: '#f6e8ec', type: 'expense' },
  { id: 'daily', name: '日用', icon: 'basket', color: '#f5eddf', type: 'expense' },
  { id: 'house', name: '住房', icon: 'home', color: '#e9e9f6', type: 'expense' },
  { id: 'play', name: '娱乐', icon: 'game', color: '#f0eafa', type: 'expense' },
  { id: 'health', name: '医疗', icon: 'health', color: '#e5f2ed', type: 'expense' },
  { id: 'study', name: '学习', icon: 'study', color: '#e8f0ed', type: 'expense' },
  { id: 'gift', name: '人情', icon: 'gift', color: '#faece5', type: 'expense' },
  { id: 'travel', name: '旅行', icon: 'plane', color: '#e7f0f8', type: 'expense' },
  { id: 'other', name: '其他', icon: 'more', color: '#eef0ef', type: 'expense' },
  { id: 'salary', name: '工资', icon: 'wallet', color: '#e3f2e9', type: 'income' },
  { id: 'bonus', name: '奖金', icon: 'gift', color: '#fbefe1', type: 'income' },
  { id: 'parttime', name: '兼职', icon: 'work', color: '#e8eefb', type: 'income' },
  { id: 'refund', name: '退款', icon: 'return', color: '#f6e8ec', type: 'income' },
  { id: 'other-income', name: '其他', icon: 'more', color: '#eef0ef', type: 'income' },
]
