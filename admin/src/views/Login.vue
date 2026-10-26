<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Lock, User } from '@element-plus/icons-vue'
import { api, auth } from '../api'
import { errorMessage } from '../format'
import type { Identity } from '../types'

const route = useRoute(), router = useRouter()
const username = ref(''), password = ref(''), busy = ref(false), error = ref('')
async function login() {
  if (busy.value) return
  if (!username.value.trim() || !password.value) { error.value = '请输入管理员账号和密码'; return }
  busy.value = true; error.value = ''
  try {
    auth.user = await api<Identity>('/auth/login', {
      method: 'POST', body: JSON.stringify({ username: username.value.trim(), password: password.value }),
    })
    auth.checked = true; password.value = ''
    const target = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    await router.replace(target.startsWith('/') && !target.startsWith('//') ? target : '/')
  } catch (e) { error.value = errorMessage(e) }
  finally { busy.value = false }
}
</script>
<template>
  <div class="login-page">
    <header class="login-header">
      <a class="brand" href="/login"><img src="/favicon.svg" alt="" /><span>墨记</span><small>管理后台</small></a>
    </header>
    <main class="login-form-wrap"><div class="login-form">
      <span class="login-mark">墨</span><h2>欢迎回来</h2><p class="muted">登录管理后台，查看墨记的最新动态。</p>
      <el-alert v-if="route.query.reason === 'expired'" title="登录已过期，请重新登录" type="warning" :closable="false" show-icon />
      <el-alert v-else-if="route.query.reason === 'connection'" title="暂时无法连接服务，请检查网络后重新登录" type="warning" :closable="false" show-icon />
      <form @submit.prevent="login">
        <label for="username">管理员账号</label><el-input id="username" v-model="username" :prefix-icon="User" size="large" placeholder="请输入管理员账号" autocomplete="username" maxlength="64" :disabled="busy" />
        <label for="password">登录密码</label><el-input id="password" v-model="password" :prefix-icon="Lock" type="password" show-password size="large" placeholder="请输入登录密码" autocomplete="current-password" maxlength="256" :disabled="busy" />
        <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" />
        <el-button type="primary" size="large" native-type="submit" :loading="busy" class="login-submit">{{ busy ? '正在登录' : '登录管理后台' }}</el-button>
      </form>
      <p class="login-note"><el-icon><Lock /></el-icon> 仅限授权管理员访问</p>
    </div><p class="login-copyright">墨记管理后台 v1.0</p></main>
  </div>
</template>

<style scoped>
/* 登录布局独立于管理页；窄屏和矮窗口按正常文档流滚动。 */
.login-page {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  min-height: 100dvh;
  min-width: 0;
  background: #f4f7f6;
}
.login-header { flex: none; padding: 24px 32px; }
.login-header .brand { width: fit-content; }
.login-form-wrap {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 20px;
  min-width: 0;
  padding: 16px 24px 32px;
}
.login-form {
  flex: none;
  width: 420px;
  max-width: 100%;
  padding: 28px 32px;
  border: 1px solid #e0e9e3;
  border-radius: 14px;
  background: #fff;
}
.login-mark {
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  margin-bottom: 18px;
  border-radius: 12px;
  background: #eef4ef;
  color: #3e6f4f;
  font: 24px serif;
}
.login-form h2 { font-size: 25px; letter-spacing: 1px; }
.login-form > .muted { margin: 8px 0 24px; font-size: 13px; }
.login-form label {
  display: block;
  margin: 20px 0 9px;
  color: #5e7766;
  font-size: 13px;
}
.login-form :deep(.el-input__wrapper) {
  padding: 5px 14px;
  background: #fcfdfb;
  box-shadow: 0 0 0 1px #dce7dd inset;
}
.login-form :deep(.el-input__inner) { font-size: 13px; }
.login-submit { width: 100%; min-height: 46px; margin-top: 28px; letter-spacing: 1px; }
.login-form form .el-alert { margin-top: 18px; }
.login-note {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  margin-top: 20px;
  color: #9bab9e;
  font-size: 11px;
}
.login-copyright { flex: none; color: #a4b0a7; font-size: 11px; text-align: center; }
@media (max-width: 480px) {
  .login-header { padding: 20px; }
  .login-form-wrap { padding: 8px 16px 24px; }
  .login-form { padding: 24px; }
}
</style>
