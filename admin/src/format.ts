export const number = (value: number) => new Intl.NumberFormat('zh-CN').format(value)
export function datetime(value?: string) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value))
}
export const shortId = (value: string) => value.slice(0, 8)
export const errorMessage = (error: unknown) => error instanceof Error ? error.message : '加载失败，请重试'
