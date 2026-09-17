import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { getTasks, startTask, stopTask } from '@/api/endpoints'
import { ElMessage } from 'element-plus'

/** 任务状态 — 对齐后端 TaskStatus 枚举 */
export type TaskStatus = 'idle' | 'running' | 'finished' | 'failed' | 'cancelled'

/** 任务项 — 对齐 GET /api/tasks 响应 */
export interface TaskItem {
  name: string
  kind: 'continuous' | 'timed'
  status: TaskStatus
  description?: string
  duration?: number
  started_at?: string
  finished_at?: string
  error?: string
}

/** 任务 store — 管理任务列表与启停操作 */
export const useTaskStore = defineStore('task', () => {
  // ── 状态 ──────────────────────────────────────────────
  const tasks = ref<TaskItem[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const operating = ref<string | null>(null)  // 当前正在操作的任务名

  // ── 计算属性 ──────────────────────────────────────────
  const runningTasks = computed(() =>
    tasks.value.filter(t => t.status === 'running'),
  )

  const runningCount = computed(() => runningTasks.value.length)

  const taskCount = computed(() => tasks.value.length)

  // ── 动作 ──────────────────────────────────────────────
  /** 从 API 加载任务列表 */
  async function fetchTasks(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const data = await getTasks()
      // API 返回 { tasks: [...] } 或直接数组
      tasks.value = Array.isArray(data) ? data : (data as Record<string, unknown>).tasks as TaskItem[] ?? []
    } catch (e) {
      error.value = e instanceof Error ? e.message : '加载任务失败'
    } finally {
      loading.value = false
    }
  }

  /** 启动任务 */
  async function handleStart(name: string): Promise<boolean> {
    operating.value = name
    try {
      await startTask(name)
      ElMessage.success(`任务 ${name} 已启动`)
      await fetchTasks()  // 刷新列表
      return true
    } catch (e) {
      const msg = e instanceof Error ? e.message : `启动 ${name} 失败`
      ElMessage.error(msg)
      return false
    } finally {
      operating.value = null
    }
  }

  /** 停止任务 */
  async function handleStop(name: string): Promise<boolean> {
    operating.value = name
    try {
      await stopTask(name)
      ElMessage.success(`任务 ${name} 已停止`)
      await fetchTasks()  // 刷新列表
      return true
    } catch (e) {
      const msg = e instanceof Error ? e.message : `停止 ${name} 失败`
      ElMessage.error(msg)
      return false
    } finally {
      operating.value = null
    }
  }

  function clearError() {
    error.value = null
  }

  return {
    tasks,
    loading,
    error,
    operating,
    runningTasks,
    runningCount,
    taskCount,
    fetchTasks,
    handleStart,
    handleStop,
    clearError,
  }
})