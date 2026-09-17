import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { requiresAuth: false, title: '登录' },
  },
  {
    path: '/',
    component: () => import('@/views/LayoutView.vue'),
    redirect: '/observe',
    children: [
      {
        path: 'observe',
        name: 'observe',
        component: () => import('@/views/ObserveView.vue'),
        meta: { title: '观测' },
      },
      {
        path: 'tasks',
        name: 'tasks',
        component: () => import('@/views/TasksView.vue'),
        meta: { title: '任务中心' },
      },
      {
        path: 'config',
        name: 'config',
        component: () => import('@/views/ConfigView.vue'),
        meta: { title: '配置' },
      },
      {
        path: 'system',
        name: 'system',
        component: () => import('@/views/SystemView.vue'),
        meta: { title: '系统' },
      },
      {
        path: 'scenarios',
        name: 'scenarios',
        component: () => import('@/views/ScenariosView.vue'),
        meta: { title: '剧本' },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 路由守卫：未登录时重定向到登录页
router.beforeEach((_to, _from, next) => {
  // 如果后端启用了认证，未登录用户访问受保护页面会被后端 302 到 /login
  // 前端不做重复拦截，让后端安全层主导
  next()
})

export default router