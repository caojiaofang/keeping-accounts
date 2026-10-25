import { API_BASE_URL, ApiError, notifyUnauthorized } from '@/common/api/request'
import type { ChatStreamEvent, ChatTurn } from '@/common/types/chat'

export interface ChatStreamTask { promise: Promise<ChatTurn>; cancel: () => void }

// 微信运行环境不保证提供 TextDecoder；保留跨分片的 UTF-8 字节。
export class StreamDecoder {
  private pending: number[] = []
  decode(buffer: ArrayBuffer, final = false): string {
    const bytes = [...this.pending, ...new Uint8Array(buffer)]
    let text = '', index = 0
    while (index < bytes.length) {
      const first = bytes[index]
      const size = first < 0x80 ? 1 : first >= 0xc2 && first < 0xe0 ? 2 : first < 0xf0 && first >= 0xe0 ? 3 : first >= 0xf0 && first <= 0xf4 ? 4 : 0
      if (!size) throw new Error('流式响应编码异常')
      if (index + size > bytes.length) break
      let code = first & (size === 1 ? 0x7f : size === 2 ? 0x1f : size === 3 ? 0x0f : 0x07)
      for (let offset = 1; offset < size; offset++) {
        const byte = bytes[index + offset]
        if ((byte & 0xc0) !== 0x80) throw new Error('流式响应编码异常')
        code = (code << 6) | (byte & 0x3f)
      }
      if ((size === 2 && code < 0x80) || (size === 3 && code < 0x800) || (size === 4 && code < 0x10000) || code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff)) throw new Error('流式响应编码异常')
      text += String.fromCodePoint(code)
      index += size
    }
    this.pending = bytes.slice(index)
    if (final && this.pending.length) throw new Error('流式响应意外中断，请重新读取对话')
    return text
  }
}

export class ChatEventParser {
  private buffer = ''
  constructor(private receive: (event: ChatStreamEvent) => void) {}
  push(text: string): void {
    this.buffer += text
    if (this.buffer.length > 1000000) throw new Error('流式响应过长')
    let boundary: RegExpExecArray | null
    while ((boundary = /\r?\n\r?\n/.exec(this.buffer))) {
      const frame = this.buffer.slice(0, boundary.index)
      this.buffer = this.buffer.slice(boundary.index + boundary[0].length)
      let event = 'message'
      const data: string[] = []
      for (const line of frame.split(/\r?\n/)) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
      }
      if (data.length && event !== 'ping') this.receive({ event, data: JSON.parse(data.join('\n')) } as ChatStreamEvent)
    }
  }
}

export function chatStream(path: string, token: string, data: Record<string, unknown>, receive: (event: ChatStreamEvent) => void): ChatStreamTask {
  let abort = () => {}, settled = false
  let rejectRequest: (error: Error) => void = () => {}
  const promise = new Promise<ChatTurn>((resolve, reject) => {
    rejectRequest = reject
    const fail = (cause: unknown) => {
      if (settled) return
      settled = true
      reject(cause instanceof Error ? cause : new Error('连接中断，请重新读取或重试这条消息'))
    }
    const parser = new ChatEventParser(event => {
      if (settled) return
      if (event.event === 'error') {
        if (event.data.status === 401) notifyUnauthorized(token)
        fail(new ApiError(event.data.detail, event.data.status))
      } else if (event.event === 'done') {
        settled = true
        resolve(event.data)
      } else receive(event)
    })
    const decoder = new StreamDecoder()
    const push = (value: ArrayBuffer) => { try { parser.push(decoder.decode(value)) } catch (error) { fail(error); abort() } }
    const finish = () => {
      try { parser.push(decoder.decode(new ArrayBuffer(0), true)) } catch (error) { fail(error) }
      if (!settled) fail(new Error('回答连接已中断，请重新读取或重试这条消息'))
    }

    // #ifdef H5
    if (typeof window !== 'undefined' && typeof fetch === 'function') {
      const controller = new AbortController()
      abort = () => controller.abort()
      const timer = setTimeout(abort, 300000)
      void (async () => {
        try {
          const response = await fetch(`${API_BASE_URL}${path}`, { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', Accept: 'text/event-stream' }, body: JSON.stringify(data), signal: controller.signal })
          if (!response.ok) {
            if (response.status === 401) notifyUnauthorized(token)
            const body = await response.json().catch(() => ({}))
            throw new ApiError(body.detail || '分析服务暂时不可用', response.status)
          }
          if (!response.body) throw new Error('当前环境不支持流式回答')
          const reader = response.body.getReader()
          try {
            while (true) {
              const chunk = await reader.read()
              if (chunk.done) break
              push(chunk.value.buffer.slice(chunk.value.byteOffset, chunk.value.byteOffset + chunk.value.byteLength) as ArrayBuffer)
            }
          } finally { reader.releaseLock() }
          finish()
        } catch (error) { fail(error) }
        finally { clearTimeout(timer) }
      })()
      return
    }
    // #endif

    let received = false
    const task = uni.request({
      url: `${API_BASE_URL}${path}`, method: 'POST', data,
      header: { Authorization: `Bearer ${token}`, Accept: 'text/event-stream', 'Content-Type': 'application/json' },
      timeout: 300000, enableChunked: true, responseType: 'arraybuffer', dataType: 'text',
      success(response) {
        if (response.statusCode < 200 || response.statusCode >= 300) {
          if (response.statusCode === 401) notifyUnauthorized(token)
          let detail = '分析服务暂时不可用'
          try {
            const body = typeof response.data === 'string' ? response.data : new StreamDecoder().decode(response.data as ArrayBuffer, true)
            detail = JSON.parse(body).detail || detail
          } catch { /* 非 JSON 错误只展示统一提示。 */ }
          fail(new ApiError(detail, response.statusCode))
          return
        }
        // 某些调试环境只在结束时给完整响应，仍解析同一份 SSE 协议。
        if (!received && response.data) {
          try { typeof response.data === 'string' ? parser.push(response.data) : push(response.data as ArrayBuffer) } catch (error) { fail(error) }
        }
        finish()
      },
      fail() { fail(new Error('回答连接已中断，请检查网络后重新读取或重试')) },
    })
    abort = () => task.abort()
    const chunkTask = task as typeof task & { onChunkReceived?: (listener: (chunk: { data: ArrayBuffer }) => void) => void }
    chunkTask.onChunkReceived?.(({ data: chunk }) => { received = true; push(chunk) })
  })
  return { promise, cancel() { if (settled) return; settled = true; abort(); rejectRequest(new Error('对话已切换')) } }
}
