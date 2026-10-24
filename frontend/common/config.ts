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
try {
  const accountInfo = uni.getAccountInfoSync()
  envVersion = accountInfo.miniProgram?.envVersion || 'develop'
} catch {
  // 非微信预览或账号信息读取失败时使用本地开发环境。
}
// #endif

export const appConfig = {
  // 仅明确识别到正式版时连接正式环境，未知版本继续使用开发地址。
  apiBaseUrl: (envVersion === 'release' ? PROD_BASE_URL : DEV_BASE_URL).replace(/\/+$/, ''),
  envVersion,
  envName: envNames.get(envVersion) || '未知版本',
  requestTimeout: 15000,
}
