<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useIntervalFn } from '@vueuse/core'
import { useTaskStore } from '@/stores/task'
import { usePolling } from '@/composables/usePolling'
import type { TaskItem, TaskStatus } from '@/stores/task'
import {
  VideoPlay,
  VideoPause,
  Refresh,
  Timer,
} from '@element-plus/icons-vue'

const taskStore = useTaskStore()

// 轮询兜底 — 每 5s 刷新任务状态（同时校准运行时长锚点）
usePolling(
  () => taskStore.fetchTasks(),
  5000,
)

// 本地逐秒 tick — 运行时长在两次轮询之间也能跳动
const now = ref(Date.now())
useIntervalFn(() => {
  now.value = Date.now()
}, 1000)

/** 显示用运行时长 = 服务器 elapsed + 本地推算增量 */
function displayElapsed(row: TaskItem): number {
  if (row.elapsed == null || row.elapsed < 0) return -1
  const drift = Math.max(0, (now.value - taskStore.fetchedAt) / 1000)
  return row.elapsed + drift
}

// 启动抽屉
const drawerVisible = ref(false)
const selectedTask = ref<TaskItem | null>(null)

function openStartDrawer(task: TaskItem | Record<string, unknown>) {
  selectedTask.value = task as TaskItem
  drawerVisible.value = true
}

async function confirmStart() {
  if (!selectedTask.value) return
  const ok = await taskStore.handleStart(selectedTask.value.name)
  if (ok) {
    drawerVisible.value = false
    selectedTask.value = null
  }
}

async function handleStop(name: string) {
  await taskStore.handleStop(name)
}

// 状态标签颜色
function statusType(status: TaskStatus): 'success' | 'warning' | 'info' | 'danger' {
  switch (status) {
    case 'running': return 'success'
    case 'stopping': return 'warning'
    case 'idle': return 'info'
    case 'finished': return 'success'
    case 'failed': return 'danger'
    case 'cancelled': return 'warning'
    default: return 'info'
  }
}

function statusLabel(status: TaskStatus): string {
  switch (status) {
    case 'running': return '运行中'
    case 'stopping': return '停止中'
    case 'idle': return '空闲'
    case 'finished': return '已完成'
    case 'failed': return '失败'
    case 'cancelled': return '已取消'
    default: return status
  }
}

// 运行时长格式化（后端 elapsed 为已运行秒数）
function formatDuration(elapsedSec?: number): string {
  if (elapsedSec == null || elapsedSec < 0) return '-'
  const sec = Math.floor(elapsedSec)
  if (sec < 60) return `${sec}秒`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}分${sec % 60}秒`
  const hr = Math.floor(min / 60)
  return `${hr}时${min % 60}分`
}

/** 限时任务进度百分比 — T1: 优先用 duration（实际启动时长），回落 default_duration */
function progressPercent(row: TaskItem): number {
  const total = row.duration ?? row.default_duration
  if (!total || total <= 0) return 0
  return Math.min(100, Math.round((displayElapsed(row) / total) * 100))
}

/** 表格行类名 — 运行中/停止中整行高亮 */
function rowClassName({ row }: { row: TaskItem }): string {
  if (row.status === 'running') return 'task-row-running'
  if (row.status === 'stopping') return 'task-row-stopping'
  return ''
}

onMounted(() => {
  taskStore.fetchTasks()
})
</script>

<template>
  <div class="tasks-view">
    <!-- 页头 -->
    <div class="page-header">
      <h2>任务中心</h2>
      <div class="header-actions">
        <el-tag type="success" v-if="taskStore.runningCount > 0" class="status-tag">
          <span class="pulse-dot" />
          {{ taskStore.runningCount }} 个运行中
        </el-tag>
        <el-button
          :icon="Refresh"
          :loading="taskStore.loading"
          @click="taskStore.fetchTasks()"
        >
          刷新
        </el-button>
      </div>
    </div>

    <!-- 任务表格 -->
    <el-table
      :data="taskStore.tasks"
      v-loading="taskStore.loading"
      :row-class-name="rowClassName"
      stripe
      style="width: 100%"
    >
      <el-table-column prop="name" label="任务名称" min-width="140">
        <template #default="{ row }">
          <span class="task-name">{{ row.name }}</span>
          <el-tag size="small" type="info" v-if="row.kind" class="kind-tag">
            {{ row.kind === 'continuous' ? '持续' : '限时' }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column prop="status" label="状态" width="130" align="center">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small" class="status-tag">
            <span v-if="row.status === 'running' || row.status === 'stopping'" class="pulse-dot" />
            {{ statusLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column label="运行时长" width="170" align="center">
        <template #default="{ row }">
          <div v-if="row.status === 'running' || row.status === 'stopping'" class="elapsed-cell">
            <span class="elapsed-text">
              <el-icon class="elapsed-icon"><Timer /></el-icon>
              {{ formatDuration(displayElapsed(row as TaskItem)) }}
            </span>
            <el-progress
              v-if="row.kind === 'timed' && row.default_duration"
              :percentage="progressPercent(row as TaskItem)"
              :stroke-width="4"
              :show-text="false"
              class="elapsed-progress"
            />
          </div>
          <span v-else class="text-muted">-</span>
        </template>
      </el-table-column>

      <el-table-column prop="description" label="说明" min-width="200" show-overflow-tooltip />

      <el-table-column label="操作" width="180" align="center" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="row.status === 'idle' || row.status === 'finished' || row.status === 'failed' || row.status === 'cancelled'"
            type="primary"
            size="small"
            :icon="VideoPlay"
            :loading="taskStore.operating === row.name"
            @click="openStartDrawer(row)"
          >
            启动
          </el-button>
          <el-button
            v-if="row.status === 'running'"
            type="danger"
            size="small"
            :icon="VideoPause"
            :loading="taskStore.operating === row.name"
            @click="handleStop(row.name)"
          >
            停止
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 启动确认抽屉 -->
    <el-drawer
      v-model="drawerVisible"
      title="启动任务"
      direction="rtl"
      size="360px"
    >
      <div v-if="selectedTask" class="drawer-content">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="任务名称">
            {{ selectedTask.name }}
          </el-descriptions-item>
          <el-descriptions-item label="类型">
            {{ selectedTask.kind === 'continuous' ? '持续运行' : '限时运行' }}
          </el-descriptions-item>
          <el-descriptions-item label="说明">
            {{ selectedTask.description || '无' }}
          </el-descriptions-item>
          <el-descriptions-item label="默认时长" v-if="selectedTask.kind === 'timed'">
            {{ (selectedTask.duration ?? selectedTask.default_duration) ? `${selectedTask.duration ?? selectedTask.default_duration}秒` : '未指定' }}
          </el-descriptions-item>
        </el-descriptions>

        <el-alert
          v-if="selectedTask.kind === 'timed'"
          title="限时任务将在指定时长后自动停止"
          type="info"
          :closable="false"
          show-icon
          class="drawer-alert"
        />

        <div class="drawer-actions">
          <el-button @click="drawerVisible = false">取消</el-button>
          <el-button
            type="primary"
            :loading="taskStore.operating === selectedTask.name"
            @click="confirmStart"
          >
            确认启动
          </el-button>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.tasks-view {
  max-width: 1200px;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}
.page-header h2 {
  margin: 0;
  font-size: 20px;
  color: var(--el-text-color-primary);
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.task-name {
  font-weight: 500;
}
.kind-tag {
  margin-left: 8px;
}
.text-muted {
  color: #c0c4cc;
}
/* ── 运行中视觉反馈 ─────────────────────────────── */
.status-tag {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.pulse-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: currentColor;
  flex-shrink: 0;
  animation: task-pulse 1.2s ease-in-out infinite;
}
@keyframes task-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.3; transform: scale(0.7); }
}
/* 运行中整行高亮（语义变量，暗色模式自动适配） */
:deep(.task-row-running td.el-table__cell) {
  background-color: var(--el-color-success-light-9) !important;
}
:deep(.task-row-stopping td.el-table__cell) {
  background-color: var(--el-color-warning-light-9) !important;
}
.elapsed-cell {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 2px 0;
}
.elapsed-text {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-primary);
}
.elapsed-icon {
  color: var(--el-color-success);
}
.elapsed-progress {
  width: 90%;
}
.drawer-content {
  padding: 0 20px 20px;
}
.drawer-alert {
  margin-top: 16px;
}
.drawer-actions {
  margin-top: 24px;
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
</style>