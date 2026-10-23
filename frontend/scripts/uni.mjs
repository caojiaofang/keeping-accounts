import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

// 与 HBuilderX 共用根目录源码及 unpackage 编译目录。
const root = fileURLToPath(new URL('../', import.meta.url))
const [mode = 'dev', platform = 'mp-weixin'] = process.argv.slice(2)
if (!['dev', 'build'].includes(mode) || !['mp-weixin', 'h5'].includes(platform)) {
  throw new Error('不支持的构建方式或平台')
}
const cli = path.join(root, 'node_modules/@dcloudio/vite-plugin-uni/bin/uni.js')
const args = mode === 'build' ? ['build', '-p', platform] : ['-p', platform]
const child = spawn(process.execPath, [cli, ...args], {
  cwd: root,
  stdio: 'inherit',
  env: {
    ...process.env,
    UNI_INPUT_DIR: root,
    UNI_OUTPUT_DIR: path.join(root, 'unpackage/dist', mode, platform),
  },
})
child.on('exit', code => { process.exitCode = code ?? 1 })
child.on('error', error => { console.error('构建进程启动失败：', error.message); process.exitCode = 1 })
