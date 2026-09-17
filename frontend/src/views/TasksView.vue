<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useTaskStore } from '@/stores/task'
import { usePolling } from '@/composables/usePolling'
import type { TaskItem, TaskStatus } from '@/stores/task'
import {
  VideoPlay,
  VideoPause,
  Refresh,
} from '@element-plus/icons-vue'

const taskStore = useTaskStore()

// 轮询兜底 — 每 5s 刷新任务状态
usePolling(
  () => taskStore.fetchTasks(),
  5000,
)

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
    case 'idle': return '空闲'
    case 'finished': return '已完成'
    case 'failed': return '失败'
    case 'cancelled': return '已取消'
    default: return status
  }
}

// 运行时长格式化
function formatDuration(startedAt?: string): string {
  if (!startedAt) return '-'
  const start = new Date(startedAt).getTime()
  const now = Date.now()
  const sec = Math.floor((now - start) / 1000)
  if (sec < 60) return `${sec}秒`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}分${sec % 60}秒`
  const hr = Math.floor(min / 60)
  return `${hr}时${min % 60}分`
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
        <el-tag type="success" v-if="taskStore.runningCount > 0">
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

      <el-table-column prop="status" label="状态" width="120" align="center">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">
            {{ statusLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column label="运行时长" width="130" align="center">
        <template #default="{ row }">
          <span v-if="row.status === 'running'">
            {{ formatDuration(row.started_at) }}
          </span>
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
            {{ selectedTask.duration ? `${selectedTask.duration}秒` : '未指定' }}
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
  color: #303133;
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