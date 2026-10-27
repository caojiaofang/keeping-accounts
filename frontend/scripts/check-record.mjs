import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createPinia, setActivePinia } from 'pinia'
import ts from 'typescript'

const root = fileURLToPath(new URL('../', import.meta.url))
const compiled = new Map()
async function moduleUrl(filename) {
  if (compiled.has(filename)) return compiled.get(filename)
  let source = ts.transpileModule(await readFile(filename, 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } }).outputText
  for (const match of [...source.matchAll(/from\s+(['"])([^'"]+)\1/g)]) {
    const specifier = match[2]
    const local = specifier.startsWith('@/') ? path.join(root, specifier.slice(2)) : specifier.startsWith('.') ? path.resolve(path.dirname(filename), specifier) : null
    const resolved = local ? await moduleUrl(local.endsWith('.ts') ? local : `${local}.ts`) : import.meta.resolve(specifier)
    source = source.replace(match[0], `from '${resolved}'`)
  }
  const url = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
  compiled.set(filename, url)
  return url
}

globalThis.uni = { getAccountInfoSync: () => ({}), showToast() {} }
const { useLedgerStore } = await import(await moduleUrl(path.join(root, 'store/ledger.ts')))
const { useRecordStore } = await import(await moduleUrl(path.join(root, 'store/record.ts')))
const { imageDataUrl, chooseRecordImage, MAX_IMAGE_BYTES } = await import(await moduleUrl(path.join(root, 'common/services/record-image.ts')))
const { recordExamples } = await import(await moduleUrl(path.join(root, 'common/mock/assistant.ts')))
const clone = value => JSON.parse(JSON.stringify(value))
const image = { path: 'local-receipt.png', dataUrl: 'data:image/png;base64,iVBORw0KGgo=' }
const draft = (key = 'draft-1', amount = 3500) => ({ key, bookId: 'book-1', type: 'expense', categoryId: 'food', amount, date: '2026-10-27', note: '午饭', account: '微信', accountDefaulted: true, status: 'pending' })
const result = drafts => ({ drafts, reply: '请核对后确认记账' })
function fresh(cloud = true) {
  setActivePinia(createPinia())
  const ledger = useLedgerStore()
  const record = useRecordStore()
  ledger.accountId = cloud ? 'user-1' : null
  ledger.activeBookId = 'book-1'
  ledger.recordDrafts = async () => result([draft()])
  ledger.saveEntry = async () => true
  return { ledger, record }
}
let passed = 0
async function check(name, action) { await action(); passed++; console.log(`通过：${name}`) }

await check('连续补充传递当前草稿及上下文，保留编号，旧卡片不可再次确认', async () => {
  const { ledger, record } = fresh()
  let writes = 0
  ledger.saveEntry = async () => { writes++; return true }
  assert.equal(await record.send('午饭35元'), true)
  const old = record.messages[1]
  ledger.recordDrafts = async input => {
    assert.equal(input.history[0].text, '午饭35元')
    assert.equal(input.drafts[0].key, 'draft-1')
    assert.equal(input.drafts[0].amount, 3500)
    assert.equal(input.drafts[0].status, undefined)
    return result([draft('draft-1', 3200)])
  }
  assert.equal(await record.send('改成32元'), true)
  assert.equal(record.pending[0].key, 'draft-1')
  assert.equal(record.pending[0].amount, 3200)
  assert.equal(old.superseded, true)
  assert.equal(writes, 0)
  assert.equal(await record.confirm(['draft-1']), 1)
  assert.equal(record.pending.length, 0)
  assert.equal(await record.confirm(['draft-1']), 0)
  assert.equal(writes, 1)
})

await check('图片缺日期时保留图片用于补充，完成后不再重复发送旧图', async () => {
  const { ledger, record } = fresh()
  let calls = 0
  ledger.recordDrafts = async input => {
    calls++
    if (calls <= 2) assert.equal(input.image, image.dataUrl)
    else assert.equal(input.image, undefined)
    return calls === 1 ? { drafts: [], reply: '是哪天的？', clarification: '是哪天的？' } : result([draft()])
  }
  await record.send('', image)
  assert.equal(record.messages[0].imagePath, image.path)
  await record.send('昨天的')
  await record.send('改成32元')
  assert.equal(calls, 3)
})

await check('追问不明确时保留原草稿，不能让空结果覆盖待确认内容', async () => {
  const { ledger, record } = fresh()
  await record.send('午饭35元')
  ledger.recordDrafts = async () => ({ drafts: [], clarification: '改哪一笔？', reply: '改哪一笔？' })
  await record.send('改一下')
  assert.equal(record.pending[0].amount, 3500)
  assert.equal(record.messages[1].superseded, undefined)
})

await check('识别失败保留原消息和草稿，重试不新增用户消息，不自动重试', async () => {
  const { ledger, record } = fresh()
  await record.send('午饭35元')
  let calls = 0
  ledger.recordDrafts = async () => { calls++; if (calls === 1) throw new Error('测试超时'); return result([draft('draft-1', 3200)]) }
  assert.equal(await record.send('改成32元', image), false)
  assert.equal(calls, 1)
  assert.equal(record.messages.length, 3)
  assert.equal(record.messages[2].failed, true)
  assert.equal(record.pending[0].amount, 3500)
  assert.equal(await record.retryLast(), true)
  assert.equal(record.messages.length, 4)
  assert.equal(record.messages[2].failed, false)
  assert.equal(record.pending[0].amount, 3200)
})

await check('保存响应丢失时锁定草稿，保留同一 ID 和原内容重试', async () => {
  const { ledger, record } = fresh()
  await record.send('午饭35元')
  const sent = []
  ledger.saveEntry = async (entry, id, requestId) => { sent.push({ entry: clone(entry), requestId }); return sent.length > 1 }
  assert.equal(await record.confirm(['draft-1']), 0)
  assert.equal(record.blocked, true)
  assert.equal(await record.send('改成32元'), false)
  record.editDraft('draft-1', draft('draft-1', 3200))
  record.dismissDraft('draft-1')
  assert.equal(record.pending[0].amount, 3500)
  assert.equal(await record.confirm(['draft-1']), 1)
  assert.deepEqual(sent[0], sent[1])
  assert.equal(record.blocked, false)
})

await check('部分确认后不再传递已保存或已忽略账单的原始描述', async () => {
  const { ledger, record } = fresh()
  ledger.recordDrafts = async () => result([draft('one'), draft('two', 1800)])
  await record.send('午饭35元，咖啡18元')
  await record.confirm(['one'])
  ledger.recordDrafts = async input => {
    assert.deepEqual(input.history, [])
    assert.deepEqual(input.drafts.map(item => item.key), ['two'])
    return result([draft('two', 1600)])
  }
  await record.send('咖啡改成16元')
  record.dismissDraft('two')
  ledger.recordDrafts = async input => {
    assert.deepEqual(input.history, [])
    assert.deepEqual(input.drafts, [])
    return result([draft('three')])
  }
  await record.send('新记一笔')
})

await check('切换账本或账号时清空文字、图片和草稿，并忽略迟到响应', async () => {
  for (const field of ['activeBookId', 'accountId']) {
    const { ledger, record } = fresh()
    let release
    ledger.recordDrafts = () => new Promise(resolve => { release = () => resolve(result([draft()])) })
    const request = record.send('午饭35元', image)
    ledger[field] = 'changed'
    assert.equal(record.messages.length, 0)
    release()
    assert.equal(await request, false)
    assert.equal(record.messages.length, 0)
    assert.equal(record.retry, undefined)
    assert.equal(record.sending, false)
  }
})

await check('等待识别时不重复发送，离开页面后的结果不能恢复草稿', async () => {
  const { ledger, record } = fresh()
  let release
  ledger.recordDrafts = () => new Promise(resolve => { release = () => resolve(result([draft()])) })
  const request = record.send('午饭35元')
  assert.equal(await record.send('午饭35元'), false)
  record.clear()
  release()
  await request
  assert.deepEqual(record.messages, [])
})

await check('手动编辑清空时间可正确提交，修改保持原账本和编号', async () => {
  const { ledger, record } = fresh()
  await record.send('午饭35元')
  record.editDraft('draft-1', { ...draft('forged', 3200), bookId: 'wrong-book', time: '' })
  ledger.recordDrafts = async input => {
    assert.equal(input.drafts[0].time, undefined)
    assert.equal(input.drafts[0].key, 'draft-1')
    assert.equal(input.drafts[0].bookId, 'book-1')
    assert.equal(input.drafts[0].amount, 3200)
    return result([draft()])
  }
  await record.send('日期不变')
})

await check('体验区不发送图片或任意文本到模型，保留预置示例', async () => {
  const { ledger, record } = fresh(false)
  ledger.recordDrafts = async () => { throw new Error('不应调用接口') }
  assert.equal(await record.send('', image), false)
  assert.equal(await record.send(recordExamples[0].text), true)
  assert.equal(record.pending[0].amount, 800)
})

await check('图片类型与体积在读取和上传前校验，取消选择不报错', async () => {
  assert.equal(imageDataUrl('/9j/AAAA'), 'data:image/jpeg;base64,/9j/AAAA')
  assert.equal(imageDataUrl('iVBORw0KGgo='), image.dataUrl)
  assert.throws(() => imageDataUrl('R0lGODlh'), /JPG/)
  assert.throws(() => imageDataUrl('A'.repeat(Math.ceil(MAX_IMAGE_BYTES / 3) * 4 + 4)), /4 MB/)
  uni.chooseMedia = options => options.fail({ errMsg: 'chooseMedia:fail cancel' })
  assert.equal(await chooseRecordImage(), undefined)
  uni.chooseMedia = options => options.fail({ errMsg: 'chooseMedia:fail auth denied' })
  await assert.rejects(chooseRecordImage(), /权限/)
  let reads = 0
  uni.chooseMedia = options => options.success({ tempFiles: [{ tempFilePath: image.path, size: 100 }] })
  uni.getFileSystemManager = () => ({ getFileInfo: options => options.success({ size: MAX_IMAGE_BYTES + 1 }), readFile() { reads++ } })
  await assert.rejects(chooseRecordImage(), /4 MB/)
  assert.equal(reads, 0)
})

console.log(`连续记账与图片检查完成：${passed} 项通过；仅使用内存数据和模拟接口。`)
