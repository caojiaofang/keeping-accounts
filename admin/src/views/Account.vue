<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Lock, Refresh, SwitchButton } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import { api, auth } from '../api'
import { datetime, errorMessage } from '../format'
import type { AdminAccount } from '../types'

const router = useRouter(), account = ref<AdminAccount>(), loading = ref(false), loadError = ref('')
const currentPassword = ref(''), newPassword = ref(''), confirmPassword = ref(''), formError = ref(''), busy = ref(false)
async function load() {
  loading.value = true; loadError.value = ''
  try { account.value = await api<AdminAccount>('/account') }
  catch (e) { loadError.value = errorMessage(e) }
  finally { loading.value = false }
}
async function finish(reason: string) {
  currentPassword.value = ''; newPassword.value = ''; confirmPassword.value = ''
  auth.user = null; auth.checked = true
  await router.replace({ path: '/login', query: { reason } })
}
async function changePassword() {
  if (busy.value) return
  formError.value = ''
  if (!currentPassword.value) { formError.value = '请输入原密码'; return }
  if (newPassword.value.length < 12 || newPassword.value.length > 256) { formError.value = '新密码长度必须为 12–256 个字符'; return }
  if (newPassword.value === currentPassword.value) { formError.value = '新密码不能与原密码相同'; return }
  if (newPassword.value !== confirmPassword.value) { formError.value = '两次输入的新密码不一致'; return }
  busy.value = true
  try {
    await api('/account/password', { method: 'POST', body: JSON.stringify({ current_password: currentPassword.value, new_password: newPassword.value }) })
    await finish('password_changed')
  } catch (e) { formError.value = errorMessage(e) }
  finally { busy.value = false }
}
async function logoutAll() {
  if (busy.value) return
  try { await ElMessageBox.confirm('这会退出包括当前页面在内的所有管理端会话，之后需要重新登录。', '退出全部会话', { confirmButtonText: '退出全部会话', cancelButtonText: '取消', type: 'warning' }) }
  catch { return }
  busy.value = true
  try { await api('/account/logout-all', { method: 'POST' }); await finish('sessions_revoked') }
  catch (e) { ElMessage.error(errorMessage(e)) }
  finally { busy.value = false }
}
onMounted(load)
</script>
<template>
  <div class="page-heading"><div><h1>账号设置</h1><p>管理登录密码和管理端会话。</p></div><el-button :icon="Refresh" :loading="loading" :disabled="busy" @click="load">刷新</el-button></div>
  <el-alert v-if="loadError" :title="loadError" type="error" :closable="false" show-icon><el-button text @click="load">重新加载</el-button></el-alert>
  <el-skeleton v-if="loading && !account" :rows="8" animated class="surface skeleton" />
  <div v-if="account" class="account-grid">
    <section class="surface settings-panel"><h2><el-icon><Lock /></el-icon> 修改登录密码</h2><p class="settings-description">修改成功后，所有管理端会话都会退出。请使用新密码重新登录。</p>
      <form class="password-form" @submit.prevent="changePassword">
        <label for="current-password">原密码</label><el-input id="current-password" v-model="currentPassword" type="password" show-password autocomplete="current-password" placeholder="请输入原密码" maxlength="256" :disabled="busy" />
        <label for="new-password">新密码</label><el-input id="new-password" v-model="newPassword" type="password" show-password autocomplete="new-password" placeholder="12–256 个字符，与原密码不同" maxlength="256" :disabled="busy" />
        <label for="confirm-password">确认新密码</label><el-input id="confirm-password" v-model="confirmPassword" type="password" show-password autocomplete="new-password" placeholder="再次输入新密码" maxlength="256" :disabled="busy" />
        <el-alert v-if="formError" :title="formError" type="error" :closable="false" show-icon role="alert" />
        <el-button type="primary" native-type="submit" :loading="busy" class="change-submit">修改密码并重新登录</el-button>
      </form>
    </section>
    <div class="settings-side"><section class="surface settings-panel"><h2>当前管理员</h2><dl class="detail-list account-details"><dt>账号</dt><dd>{{ account.username }}</dd><dt>创建时间</dt><dd>{{ datetime(account.created_at) }}</dd><dt>最近登录</dt><dd>{{ datetime(account.last_login_at ?? undefined) }}</dd><dt>密码修改</dt><dd>{{ account.password_changed_at ? datetime(account.password_changed_at) : '尚未修改初始密码' }}</dd></dl><RouterLink to="/login-logs" class="settings-link">查看登录日志</RouterLink></section>
      <section class="surface settings-panel"><h2>管理登录会话</h2><p class="settings-description">退出全部会话后，其他浏览器和设备也需要重新登录。</p><el-button :icon="SwitchButton" :disabled="busy" @click="logoutAll">退出全部会话</el-button></section>
    </div>
  </div>
</template>
<style scoped>
.account-grid{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(320px,.8fr);gap:24px;align-items:start}.settings-panel{padding:26px 28px}.settings-panel h2{display:flex;align-items:center;gap:10px}.settings-description{color:#83958a;font-size:13px;line-height:1.8;margin-top:12px;margin-bottom:24px;max-width:55ch}.password-form{max-width:520px}.password-form label{display:block;font-size:13px;color:#637d6c;margin:20px 0 9px}.password-form .el-input{width:100%}.password-form .el-alert{margin-top:20px}.change-submit{margin-top:24px}.settings-side{display:grid;gap:24px}.account-details{margin-top:26px;margin-bottom:20px;grid-template-columns:85px minmax(0,1fr)}.account-details dd{overflow-wrap:anywhere}.settings-link{color:#285e4e;font-size:13px}.settings-link:hover{text-decoration:underline}@media(max-width:1000px){.account-grid{grid-template-columns:minmax(0,1fr)}}@media(max-width:480px){.settings-panel{padding:22px 18px}.change-submit{width:100%}}
</style>
