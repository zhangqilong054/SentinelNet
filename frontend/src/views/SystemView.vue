<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { getHealth, getModels, trainModel, getTrainStatus, deleteModel, analyzeTls } from '@/api/endpoints'
import { usePolling } from '@/composables/usePolling'
import { ElMessage, ElMessageBox } from 'element-plus'

// ── 健康状态 ──────────────────────────────────────────
// 后端契约 HealthResponse：{ status, timestamp, uptime_seconds, components? }（无 version 字段）
const health = ref<{ status: string; timestamp: string; uptime_seconds: number } | null>(null)

async function fetchHealth() {
  try {
    const data = await getHealth()
    if (data) health.value = data
  } catch {
    health.value = null
  }
}

function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  if (h > 0) return `${h}时${m}分${s}秒`
  if (m > 0) return `${m}分${s}秒`
  return `${s}秒`
}

// ── 模型管理 ──────────────────────────────────────────
const models = ref<Array<{ name: string; version: string; created_at: string; metrics?: Record<string, unknown> }>>([])
const modelsLoading = ref(false)

async function fetchModels() {
  modelsLoading.value = true
  try {
    const data = await getModels()
    if (data) {
      // 支持数组或 { models: [...] } 格式
      models.value = Array.isArray(data) ? data : (data as { models: typeof models.value }).models ?? []
    }
  } catch {
    ElMessage.error('加载模型列表失败')
  } finally {
    modelsLoading.value = false
  }
}

async function handleDeleteModel(name: string) {
  try {
    await ElMessageBox.confirm(`确定删除模型 "${name}"？此操作不可恢复。`, '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
    await deleteModel(name)
    ElMessage.success(`模型 "${name}" 已删除`)
    await fetchModels()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除模型失败')
  }
}

// ── 训练 ──────────────────────────────────────────────
const trainForm = ref({ dataset: '', epochs: 10 })
const training = ref(false)
const trainStatus = ref<{ status: string; progress: number; epoch: number; total_epochs: number; error?: string | null } | null>(null)

// 训练状态轮询
const { pause: pauseTrainPolling, resume: resumeTrainPolling } = usePolling(
  () => getTrainStatus(),
  3000,
  { immediate: false },
)

async function handleTrain() {
  if (!trainForm.value.dataset.trim()) {
    ElMessage.warning('请输入数据集路径')
    return
  }
  training.value = true
  try {
    // confirm 为后端必填确认位（训练会覆盖模型产物）
    await trainModel({ dataset: trainForm.value.dataset.trim(), epochs: trainForm.value.epochs, confirm: true })
    ElMessage.success('训练已启动')
    resumeTrainPolling()
  } catch {
    ElMessage.error('启动训练失败')
  } finally {
    training.value = false
  }
}

// ── TLS 分析 ──────────────────────────────────────────
const tlsAnalyzing = ref(false)
const tlsResult = ref<{ anomalies: Record<string, unknown>[]; summary: Record<string, unknown> } | null>(null)

async function handleTlsAnalyze() {
  tlsAnalyzing.value = true
  tlsResult.value = null
  try {
    const result = await analyzeTls()
    if (result) {
      tlsResult.value = {
        anomalies: (result.anomalies as Record<string, unknown>[]) ?? [],
        summary: (result.summary as Record<string, unknown>) ?? {},
      }
    }
  } catch {
    ElMessage.error('TLS 分析失败')
  } finally {
    tlsAnalyzing.value = false
  }
}

// ── 初始化 ────────────────────────────────────────────
onMounted(() => {
  fetchHealth()
  fetchModels()
  getTrainStatus().then(data => {
    if (data && data.status === 'training') {
      trainStatus.value = data
      resumeTrainPolling()
    }
  }).catch(() => {})
})

onUnmounted(() => {
  pauseTrainPolling()
})
</script>

<template>
  <div class="system-view">
    <!-- 健康状态 -->
    <el-card shadow="hover" class="system-card">
      <template #header><span>系统状态</span></template>
      <div v-if="health" class="health-info">
        <el-descriptions :column="3" border>
          <el-descriptions-item label="状态">
            <el-tag :type="health.status === 'ok' || health.status === 'healthy' ? 'success' : 'danger'">
              {{ health.status === 'ok' || health.status === 'healthy' ? '正常' : health.status }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="时间戳">{{ health.timestamp }}</el-descriptions-item>
          <el-descriptions-item label="运行时间">{{ formatUptime(health.uptime_seconds) }}</el-descriptions-item>
        </el-descriptions>
      </div>
      <el-empty v-else description="无法获取系统状态" />
    </el-card>

    <!-- 模型管理 -->
    <el-card shadow="hover" class="system-card">
      <template #header>
        <div class="card-header">
          <span>模型管理</span>
          <el-button size="small" @click="fetchModels" :loading="modelsLoading">刷新</el-button>
        </div>
      </template>

      <el-table :data="models" stripe v-loading="modelsLoading" empty-text="暂无模型">
        <el-table-column prop="name" label="名称" min-width="120" />
        <el-table-column prop="version" label="版本" width="100" />
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">
            {{ new Date(row.created_at).toLocaleString('zh-CN', { hour12: false }) }}
          </template>
        </el-table-column>
        <el-table-column label="指标" min-width="150">
          <template #default="{ row }">
            <span v-if="row.metrics">{{ JSON.stringify(row.metrics) }}</span>
            <el-tag v-else size="small" type="info">无</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80" align="center">
          <template #default="{ row }">
            <el-button type="danger" size="small" link @click="handleDeleteModel(row.name)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 模型训练 -->
    <el-card shadow="hover" class="system-card">
      <template #header><span>模型训练</span></template>

      <el-form label-width="100px" :inline="false">
        <el-form-item label="数据集路径">
          <el-input v-model="trainForm.dataset" placeholder="如: data/train_data.csv" />
        </el-form-item>
        <el-form-item label="训练轮数">
          <el-input-number v-model="trainForm.epochs" :min="1" :max="100" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="handleTrain" :loading="training">启动训练</el-button>
        </el-form-item>
      </el-form>

      <!-- 训练进度 -->
      <div v-if="trainStatus" class="train-progress">
        <el-divider>训练进度</el-divider>
        <el-descriptions :column="2" border>
          <el-descriptions-item label="状态">
            <el-tag :type="trainStatus.status === 'completed' ? 'success' : trainStatus.status === 'failed' ? 'danger' : 'warning'">
              {{ trainStatus.status }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="进度">
            <el-progress :percentage="Math.round(trainStatus.progress * 100)" :status="trainStatus.status === 'completed' ? 'success' : trainStatus.status === 'failed' ? 'exception' : undefined" />
          </el-descriptions-item>
          <el-descriptions-item label="当前轮次">{{ trainStatus.epoch }} / {{ trainStatus.total_epochs }}</el-descriptions-item>
          <el-descriptions-item v-if="trainStatus.error" label="错误">
            <span class="text-danger">{{ trainStatus.error }}</span>
          </el-descriptions-item>
        </el-descriptions>
      </div>
    </el-card>

    <!-- TLS 分析 -->
    <el-card shadow="hover" class="system-card">
      <template #header><span>TLS 分析</span></template>
      <el-button type="primary" @click="handleTlsAnalyze" :loading="tlsAnalyzing">执行 TLS 分析</el-button>

      <div v-if="tlsResult" class="tls-result">
        <el-divider>分析结果</el-divider>
        <div v-if="tlsResult.anomalies.length > 0">
          <h4>异常发现 ({{ tlsResult.anomalies.length }})</h4>
          <el-table :data="tlsResult.anomalies" stripe size="small" max-height="200">
            <el-table-column
              v-for="(_, key) in tlsResult.anomalies[0]"
              :key="key"
              :prop="String(key)"
              :label="String(key)"
              show-overflow-tooltip
            />
          </el-table>
        </div>
        <el-empty v-else description="未发现 TLS 异常" />
        <div v-if="Object.keys(tlsResult.summary).length > 0" style="margin-top: 12px">
          <h4>摘要</h4>
          <pre class="json-summary">{{ JSON.stringify(tlsResult.summary, null, 2) }}</pre>
        </div>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.system-view {
  max-width: 900px;
}
.system-card {
  margin-bottom: 16px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.health-info {
  margin-bottom: 8px;
}
.train-progress {
  margin-top: 8px;
}
.text-danger {
  color: #f56c6c;
}
.tls-result h4 {
  margin: 0 0 8px;
  font-size: 14px;
  color: #303133;
}
.json-summary {
  background: #f5f7fa;
  padding: 8px 12px;
  border-radius: 4px;
  font-size: 12px;
  overflow-x: auto;
}
</style>