import { createRouter, createWebHistory } from 'vue-router'
import { auth, restoreSession } from './api'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: () => import('./views/Login.vue'), meta: { title: '管理员登录' } },
    {
      path: '/', component: () => import('./views/Layout.vue'),
      children: [
        { path: '', name: 'overview', component: () => import('./views/Overview.vue'), meta: { title: '数据概览' } },
        { path: 'users', name: 'users', component: () => import('./views/Users.vue'), meta: { title: '用户查询' } },
        { path: 'books', name: 'books', component: () => import('./views/Books.vue'), meta: { title: '账本查询' } },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach(async to => {
  if (!auth.checked && to.path !== '/login') {
    try { await restoreSession() }
    catch { return { path: '/login', query: { reason: 'connection' } } }
  }
  if (to.path !== '/login' && !auth.user) return { path: '/login', query: { redirect: to.fullPath } }
  if (to.path === '/login' && auth.user) return '/'
  document.title = `${String(to.meta.title || '管理后台')} · 墨记`
})
window.addEventListener('moji-session-expired', () => {
  if (router.currentRoute.value.path !== '/login') {
    void router.replace({ path: '/login', query: { reason: 'expired' } })
  }
})
