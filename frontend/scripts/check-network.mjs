import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import vm from 'node:vm'
import ts from 'typescript'
import { moduleUrl, root } from './test-modules.mjs'

const source = await readFile(path.join(root, 'common/config.ts'), 'utf8')
const productionUrl = 'https://mjzs.zf-yun.cn'
const localUrl = 'http://127.0.0.1:8000'
const configuredDevelopmentUrl = source.match(/const DEV_BASE_URL = ['"]([^'"]+)['"]/)?.[1]
assert.ok(configuredDevelopmentUrl, '需要在配置中声明开发服务地址')
let passed = 0
async function check(name, action) { await action(); passed++; console.log(`通过：${name}`) }

function config({ production = false, wechat = true, developmentUrl = configuredDevelopmentUrl, account = () => ({ miniProgram: { envVersion: 'develop' } }) } = {}) {
  // 按目标平台移除条件编译块，并使用真实模块验证不同编译模式与微信环境的地址选择。
  const platformSource = source.replace(/\/\/ #ifdef MP-WEIXIN([\s\S]*?)\/\/ #endif/g, (_, content) => wechat ? content : '')
    .replace(/const DEV_BASE_URL = ['"][^'"]+['"]/, `const DEV_BASE_URL = ${JSON.stringify(developmentUrl)}`)
    .replace('import.meta.env?.PROD', String(production))
  const compiled = ts.transpileModule(platformSource, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText
  const context = { exports: {}, uni: { getAccountInfoSync: account } }
  vm.runInNewContext(compiled, context)
  return context.exports.appConfig
}

await check('发行包在开发、体验、正式及未知微信环境中始终连接正式服务', () => {
  for (const envVersion of ['develop', 'trial', 'release', 'unknown', '']) {
    assert.equal(config({ production: true, account: () => ({ miniProgram: { envVersion } }) }).apiBaseUrl, productionUrl)
  }
})

await check('微信环境信息缺失或读取失败时不会退回 localhost', () => {
  for (const production of [false, true]) {
    for (const account of [() => ({}), () => ({ miniProgram: {} }), () => { throw new Error('不可用') }]) {
      assert.equal(config({ production, account }).apiBaseUrl, productionUrl)
    }
  }
})

await check('明确开发构建使用配置的开发地址，体验与正式环境始终使用正式地址', () => {
  // 开发地址允许为 localhost、真机可访问的局域网地址或正式地址，不改写用户配置。
  for (const developmentUrl of [configuredDevelopmentUrl, localUrl, 'http://192.168.1.8:8000/']) {
    assert.equal(config({ developmentUrl }).apiBaseUrl, developmentUrl.replace(/\/+$/, ''))
    for (const envVersion of ['trial', 'release', 'future']) {
      assert.equal(config({ developmentUrl, account: () => ({ miniProgram: { envVersion } }) }).apiBaseUrl, productionUrl)
    }
    assert.equal(config({ developmentUrl, wechat: false }).apiBaseUrl, developmentUrl.replace(/\/+$/, ''))
    assert.equal(config({ developmentUrl, wechat: false, production: true }).apiBaseUrl, productionUrl)
  }
})

const { apiRequest, ApiError } = await import(await moduleUrl(path.join(root, 'common/api/request.ts')))
await check('域名、TLS、超时与离线失败分别保留类别，诊断日志不含登录凭据', async () => {
  const warnings = []
  const warn = console.warn
  console.warn = (...args) => warnings.push(args)
  try {
    for (const [errMsg, failure, message] of [
      ['request:fail url not in domain list', 'domain', /配置异常/],
      ['request:fail ssl hand shake error', 'tls', /安全连接/],
      ['request:fail timeout', 'timeout', /超时/],
      ['request:fail', 'network', /连接不到服务/],
    ]) {
      globalThis.uni = { request(options) { options.fail({ errMsg }) } }
      await assert.rejects(apiRequest('/api/v1/auth/wechat/login?ticket=private-query', { method: 'POST', data: { code: 'private-code' }, token: 'private-token' }), error => {
        assert.ok(error instanceof ApiError)
        assert.equal(error.status, 0)
        assert.equal(error.networkFailure, failure)
        assert.match(error.message, message)
        return true
      })
    }
    assert.equal(warnings.length, 4)
    assert.doesNotMatch(JSON.stringify(warnings), /private-code|private-token|private-query/)
    assert.ok(warnings.every(([, detail]) => detail.path === '/api/v1/auth/wechat/login'))
  } finally { console.warn = warn }
})

await check('HTTP 业务错误保持服务端提示，不误判为网络连接失败', async () => {
  globalThis.uni = { request(options) { options.success({ statusCode: 503, data: { detail: '微信服务繁忙，请稍后重试' } }) } }
  await assert.rejects(apiRequest('/api/v1/auth/wechat/login'), error => {
    assert.equal(error.status, 503)
    assert.equal(error.networkFailure, undefined)
    assert.equal(error.message, '微信服务繁忙，请稍后重试')
    return true
  })
})

console.log(`网络与发布环境检查完成：${passed} 项通过；未发送真实请求。`)
