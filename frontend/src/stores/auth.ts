import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { apiPost, apiGet } from '@/api/client'

/** 认证 store — 管理登录状态与用户信息 */
export const useAuthStore = defineStore('auth', () => {
  const user = ref<string | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  const isLoggedIn = computed(() => !!user.value)

  /** 登录 */
  async function login(username: string, password: string): Promise<boolean> {
    loading.value = true
    error.value = null
    try {
      const res = await apiPost<{ message: string; user?: string }>('/api/login', { username, password })
      if (res.user) {
        user.value = res.user
      } else {
        user.value = username
      }
      return true
    } catch (e) {
      const msg = e instanceof Error ? e.message : '登录失败'
      error.value = msg
      return false
    } finally {
      loading.value = false
    }
  }

  /** 登出 */
  async function logout(): Promise<void> {
    try {
      await apiPost('/api/logout')
    } catch {
      // 即使 API 失败也清除本地状态
    }
    user.value = null
  }

  /** 检查当前会话 */
  async function checkSession(): Promise<void> {
    try {
      await apiGet<{ user?: string }>('/api/health')
      // health 端点不返回用户信息，但 200 表示会话有效
      // 如果后端有 /api/me 端点可以在此使用
    } catch {
      user.value = null
    }
  }

  function clearError() {
    error.value = null
  }

  return {
    user,
    loading,
    error,
    isLoggedIn,
    login,
    logout,
    checkSession,
    clearError,
  }
})