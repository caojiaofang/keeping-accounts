<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { DataAnalysis, User, Notebook, SwitchButton, Menu, Close, Lock, Setting, Clock } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { api, auth } from '../api'
import { errorMessage } from '../format'

const route = useRoute(), router = useRouter(), menuOpen = ref(false), busy = ref(false)
const navigation = [{ path: '/', name: '数据概览', icon: DataAnalysis }, { path: '/users', name: '用户查询', icon: User }, { path: '/books', name: '账本查询', icon: Notebook }, { path: '/account', name: '账号设置', icon: Setting }, { path: '/login-logs', name: '登录日志', icon: Clock }]
async function logout() {
  busy.value = true
  try { await api('/auth/logout', { method: 'POST' }); auth.user = null; auth.checked = true; await router.replace('/login') }
  catch (e) { ElMessage.error(errorMessage(e)) }
  finally { busy.value = false }
}
</script>
<template>
  <div class="app-shell">
    <div v-if="menuOpen" class="sidebar-backdrop" @click="menuOpen = false"></div>
    <aside class="sidebar" :class="{ open: menuOpen }">
      <RouterLink to="/" class="brand" @click="menuOpen = false"><img src="/favicon.svg" alt="" /><span>墨记</span><small>管理后台</small></RouterLink>
      <div class="nav-label">工作空间</div>
      <nav aria-label="主导航"><RouterLink v-for="item in navigation" :key="item.path" :to="item.path" class="nav-item" :class="{ active: route.path === item.path }" :aria-current="route.path === item.path ? 'page' : undefined" @click="menuOpen = false"><el-icon :size="20"><component :is="item.icon" /></el-icon>{{ item.name }}</RouterLink></nav>
      <div class="sidebar-bottom"><el-icon><Lock /></el-icon><div>数据访问受保护<small>业务数据仅支持查询</small></div></div>
    </aside>
    <div class="main-column">
      <header class="topbar"><div class="topbar-left"><el-button class="mobile-menu" text :icon="menuOpen ? Close : Menu" aria-label="切换导航" @click="menuOpen = !menuOpen" /><span>管理后台</span><span class="breadcrumb-divider">/</span><strong>{{ route.meta.title }}</strong></div><div class="account"><span class="avatar">管</span><span>{{ auth.user?.username }}</span><el-button text :icon="SwitchButton" :loading="busy" @click="logout">退出</el-button></div></header>
      <main class="workspace"><RouterView :key="route.path" /></main>
      <footer class="app-footer"><span>墨记 · 把日子，一笔一笔记好</span><span>北京时间 UTC+8</span></footer>
    </div>
  </div>
</template>
