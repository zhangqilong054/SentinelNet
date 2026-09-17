/**
 * 轮询 composable — SSE 的兜底方案。
 *
 * T3.4 设计：SSE 是主通道，但某些场景需要轮询兜底：
 * 1. 健康检查（/api/health）— 每 30s
 * 2. 任务状态（/api/tasks）— SSE 不推送任务更新时兜底
 *
 * 使用 @vueuse/core 的 useIntervalFn（自动清理）。
 */

import { useIntervalFn } from '@vueuse/core'
import { ref, type Ref } from 'vue'

/** 通用轮询 hook */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number = 5000,
  options?: { immediate?: boolean },
) {
  const data = ref<T | null>(null) as Ref<T | null>
  const error = ref<Error | null>(null)
  const loading = ref(false)

  async function execute() {
    try {
      loading.value = true
      data.value = await fetcher()
      error.value = null
    } catch (e) {
      error.value = e instanceof Error ? e : new Error(String(e))
    } finally {
      loading.value = false
    }
  }

  const { pause, resume, isActive } = useIntervalFn(
    execute,
    intervalMs,
    { immediate: options?.immediate ?? true, immediateCallback: true },
  )

  return { data, error, loading, execute, pause, resume, isActive }
}