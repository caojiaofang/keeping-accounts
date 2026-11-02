// 前端只保存公开地址，微信 AppSecret 始终由后端管理。
const PROD_BASE_URL = 'https://mjzs.zf-yun.cn'
// 真机开发时改为手机可访问的本地开发服务器地址。
const DEV_BASE_URL = 'http://127.0.0.1:8000'

const envNames = new Map([
  ['develop', '开发版'],
  ['trial', '体验版'],
  ['release', '正式版'],
])

let envVersion = 'develop'
// #ifdef MP-WEIXIN
envVersion = 'unknown'
try {
  const accountInfo = uni.getAccountInfoSync()
  envVersion = accountInfo.miniProgram?.envVersion || 'unknown'
} catch {
  // 微信环境信息不可用时连接正式服务，避免审核或真机请求手机自身的 localhost。
}
// #endif

const isProductionBuild = import.meta.env?.PROD === true

export const appConfig = {
  // 发行包始终连接正式服务；只有开发构建且明确为开发环境时才允许使用本地地址。
  apiBaseUrl: (!isProductionBuild && envVersion === 'develop' ? DEV_BASE_URL : PROD_BASE_URL).replace(/\/+$/, ''),
  envVersion,
  envName: envNames.get(envVersion) || '未知版本',
  requestTimeout: 15000,
}
