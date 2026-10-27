import type { RecordImage } from '@/common/types/record'

export const MAX_IMAGE_BYTES = 4 * 1024 * 1024
const MAX_BASE64_LENGTH = Math.ceil(MAX_IMAGE_BYTES / 3) * 4

export function imageDataUrl(base64: string): string {
  if (!base64 || base64.length > MAX_BASE64_LENGTH) throw new Error('图片不能超过 4 MB，请裁剪或压缩后再试')
  const bytes = base64.length * 3 / 4 - (base64.endsWith('==') ? 2 : base64.endsWith('=') ? 1 : 0)
  if (bytes > MAX_IMAGE_BYTES) throw new Error('图片不能超过 4 MB，请裁剪或压缩后再试')
  const mime = base64.startsWith('/9j/') ? 'image/jpeg' : base64.startsWith('iVBORw0KGgo') ? 'image/png' : base64.startsWith('UklGR') ? 'image/webp' : ''
  if (!mime) throw new Error('请选择 JPG、PNG 或 WebP 图片')
  return `data:${mime};base64,${base64}`
}

function selectionError(cause: { errMsg?: string }): Error | null {
  if (cause.errMsg?.toLowerCase().includes('cancel')) return null
  return new Error('无法选择图片，请检查相册或相机权限后重试')
}

export async function chooseRecordImage(): Promise<RecordImage | undefined> {
  let selected: { path: string; size: number } | undefined
  // #ifdef H5
  if (typeof window !== 'undefined') {
    selected = await new Promise((resolve, reject) => uni.chooseImage({
      count: 1, sizeType: ['compressed'], sourceType: ['album', 'camera'],
      success(result) {
        const file = (result.tempFiles as UniApp.ChooseImageSuccessCallbackResult['tempFiles'] & { path: string; size: number }[])[0]
        resolve(file ? { path: file.path, size: file.size } : undefined)
      },
      fail(cause) { const error = selectionError(cause); if (error) reject(error); else resolve(undefined) },
    }))
    if (!selected) return undefined
    if (selected.size > MAX_IMAGE_BYTES) throw new Error('图片不能超过 4 MB，请裁剪或压缩后再试')
    const response = await fetch(selected.path)
    if (!response.ok) throw new Error('图片读取失败，请重新选择')
    const blob = await response.blob()
    if (blob.size > MAX_IMAGE_BYTES) throw new Error('图片不能超过 4 MB，请裁剪或压缩后再试')
    const base64 = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result).split(',')[1] || '')
      reader.onerror = () => reject(new Error('图片读取失败，请重新选择'))
      reader.readAsDataURL(blob)
    })
    return { path: selected.path, dataUrl: imageDataUrl(base64) }
  }
  // #endif
  selected = await new Promise((resolve, reject) => uni.chooseMedia({
    count: 1, mediaType: ['image'], sourceType: ['album', 'camera'], sizeType: ['compressed'],
    success(result) {
      const file = result.tempFiles[0]
      resolve(file ? { path: file.tempFilePath, size: file.size } : undefined)
    },
    fail(cause) { const error = selectionError(cause); if (error) reject(error); else resolve(undefined) },
  }))
  if (!selected) return undefined
  if (selected.size > MAX_IMAGE_BYTES) {
    const compressed = await new Promise<string>((resolve, reject) => uni.compressImage({
      src: selected!.path, quality: 80,
      success: result => resolve(result.tempFilePath),
      fail: () => reject(new Error('图片压缩失败，请裁剪后重新选择')),
    }))
    selected = { path: compressed, size: 0 }
  }
  const path = selected.path
  // 压缩后先检查大小，再读取，避免把超大文件完整读入内存。
  await new Promise<void>((resolve, reject) => uni.getFileSystemManager().getFileInfo({
    filePath: path,
    success(result) { result.size <= MAX_IMAGE_BYTES ? resolve() : reject(new Error('图片压缩后仍超过 4 MB，请裁剪后再试')) },
    fail: () => reject(new Error('图片读取失败，请重新选择')),
  }))
  const base64 = await new Promise<string>((resolve, reject) => uni.getFileSystemManager().readFile({
    filePath: path, encoding: 'base64',
    success: result => resolve(result.data as string),
    fail: () => reject(new Error('图片读取失败，请重新选择')),
  }))
  return { path, dataUrl: imageDataUrl(base64) }
}
