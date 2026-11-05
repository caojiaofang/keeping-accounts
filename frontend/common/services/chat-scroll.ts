// uni-app 的 scroll 事件使用 px；上滑立即停止跟随，下滑接近底部再恢复。
export function followsLatest(previousTop: number, top: number, height: number, viewport: number, following: boolean): boolean {
  if (top < previousTop - 4) return false
  if (viewport > 0 && height - top - viewport <= 64) return true
  return following
}
