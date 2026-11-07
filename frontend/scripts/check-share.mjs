import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { moduleUrl, root } from './test-modules.mjs'

const { appShare, bookShare } = await import(await moduleUrl(path.join(root, 'common/services/share.ts')))
const pages = JSON.parse(await readFile(path.join(root, 'pages.json'), 'utf8')).pages
let passed = 0
async function check(name, action) { await action(); passed++; console.log('通过：' + name) }
const invitation = { from: 'button', loggedIn: true, owner: true, inviting: true, valid: true, token: 's1_test-invitation', bookName: '家庭账本' }

await check('普通分享始终打开首页，使用固定标题和封面，不携带当前页面参数', () => {
  const card = appShare({ token: 'private-token', bookId: 'private-book', entryId: 'private-entry' })
  assert.equal(card.path, '/pages/ledger/index')
  assert.equal(card.imageUrl, '/static/share-cover.png')
  assert.doesNotMatch(JSON.stringify(card), /private-/)
  card.path = '/changed'
  assert.equal(appShare().path, '/pages/ledger/index')
})
await check('共同账本右上角分享即使存在有效邀请，也只发送小程序首页', () => {
  assert.deepEqual(bookShare({ ...invitation, from: 'menu' }), appShare())
})
await check('只有有效创建者主动点击邀请按钮，才发送已编码的账本邀请', () => {
  const result = bookShare({ ...invitation, token: 'token&extra=value' })
  assert.equal(result.title, '邀请你加入「家庭账本」')
  assert.equal(result.path, '/pages/book-invite/index?token=token%26extra%3Dvalue')
  for (const change of [{ loggedIn: false }, { owner: false }, { inviting: false }, { valid: false }, { token: '' }]) {
    assert.deepEqual(bookShare({ ...invitation, ...change }), appShare())
  }
})
await check('所有已注册页面直接注册分享生命周期，避免仅定义辅助函数而菜单仍被禁用', async () => {
  for (const page of pages) {
    const source = await readFile(path.join(root, page.path + '.vue'), 'utf8')
    assert.match(source, /import \{[^}]*\bonShareAppMessage\b[^}]*\} from '@dcloudio\/uni-app'/, page.path)
    assert.equal((source.match(/\bonShareAppMessage\(/g) || []).length, 1, page.path)
  }
})
await check('固定分享封面为随包发布的 PNG，宽高比例为 5:4', async () => {
  const cover = await readFile(path.join(root, 'static/share-cover.png'))
  assert.equal(cover.subarray(1, 4).toString(), 'PNG')
  assert.equal(cover.readUInt32BE(16) / cover.readUInt32BE(20), 5 / 4)
})
if (process.argv.includes('--build')) {
  await check('微信发行产物中所有页面均实际启用 onShareAppMessage 原生钩子，封面完整复制', async () => {
    for (const page of pages) {
      const code = await readFile(path.join(root, 'unpackage/dist/build/mp-weixin', page.path + '.js'), 'utf8')
      const flags = [...code.matchAll(/\.__runtimeHooks\s*=\s*(\d+)/g)].map(match => Number(match[1]))
      assert.ok(flags.some(value => (value & 2) !== 0), page.path)
    }
    assert.deepEqual(await readFile(path.join(root, 'unpackage/dist/build/mp-weixin/static/share-cover.png')), await readFile(path.join(root, 'static/share-cover.png')))
  })
}
console.log(`微信分享检查完成：${passed} 项通过，覆盖 ${pages.length} 个页面。`)
