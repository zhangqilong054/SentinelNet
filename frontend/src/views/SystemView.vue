<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted } from 'vue'
import { useIntervalFn } from '@vueuse/core'
import { getHealth, getModels, trainModel, getTrainStatus, deleteModel, analyzeTls, getCheck } from '@/api/endpoints'
import { usePolling } from '@/composables/usePolling'
import { ElMessage, ElMessageBox } from 'element-plus'

// ── 健康状态 ──────────────────────────────────────────
// 后端契约 HealthResponse：{ status, timestamp, uptime_seconds, components? }（无 version 字段）
const health = ref<{ status: string; timestamp: string; uptime_seconds: number; components?: Record<string, unknown> } | null>(null)
const healthLoading = ref(true)

// ── 抓包诊断（T3）──────────────────────────────────────────
// 从 /api/health.components.capture 和 /api/check.capture 聚合
interface CaptureDiagnostics {
  status: string
  iface?: string
  iface_source?: string
  filter?: string
  queue_size?: number
  dropped_packets?: number
  is_admin?: boolean
  candidate_count?: number
  enhanced?: {
    status?: string
    packets?: number
    flows?: number
    error?: string
  }
}
const captureDiag = ref<CaptureDiagnostics | null>(null)

// 运行时间实时显示：以最近一次成功拉取为锚点（服务器值 + 本机流逝秒），
// 每秒本地推算刷新，每 30s 向服务器校准一次——避免逐秒打 /api/health。
const uptimeAnchor = ref<{ seconds: number; at: number } | null>(null)
const uptimeText = ref('')

async function fetchHealth() {
  healthLoading.value = true
  try {
    const data = await getHealth()
    if (data) {
      health.value = data
      uptimeAnchor.value = { seconds: Math.floor(data.uptime_seconds), at: Date.now() }
      // T3: 从 health.components.capture 提取抓包诊断
      const captureComp = (data.components as Record<string, Record<string, unknown>> | undefined)?.capture
      if (captureComp) {
        captureDiag.value = {
          status: String(captureComp.status ?? 'unknown'),
          iface: captureComp.iface as string | undefined,
          iface_source: captureComp.iface_source as string | undefined,
          filter: captureComp.filter as string | undefined,
          queue_size: captureComp.queue_size as number | undefined,
          dropped_packets: captureComp.dropped_packets as number | undefined,
          enhanced: captureComp.enhanced as CaptureDiagnostics['enhanced'],
        }
      }
    }
  } catch {
    health.value = null
  } finally {
    healthLoading.value = false
  }
}

/** ISO 时间戳 → 本地可读格式（zh-CN，24 小时制） */
function formatDatetime(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('zh-CN', { hour12: false })
}

function formatUptime(seconds: number): string {
  const total = Math.floor(seconds) // 后端返回浮点秒，取整避免长小数
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h > 0) return `${h}时${m}分${s}秒`
  if (m > 0) return `${m}分${s}秒`
  return `${s}秒`
}

// 服务器校准轮询（30s，与 usePolling 注释约定的健康检查频率一致）
const { pause: pauseHealthPolling, resume: resumeHealthPolling } = usePolling(
  fetchHealth,
  30000,
  { immediate: false },
)
// 本地逐秒推算显示值
const { pause: pauseUptimeTick, resume: resumeUptimeTick } = useIntervalFn(
  () => {
    if (uptimeAnchor.value) {
      uptimeText.value = formatUptime(uptimeAnchor.value.seconds + (Date.now() - uptimeAnchor.value.at) / 1000)
    }
  },
  1000,
  { immediate: false },
)

// ── 模型管理 ──────────────────────────────────────────
const models = ref<Array<{ name: string; version: string; created_at: string; metrics?: Record<string, unknown>; is_best?: boolean }>>([])
const modelsLoading = ref(false)

/** 指标展示顺序与中文标签（顺序即展示顺序，其余键追加在后） */
const METRIC_LABELS: Record<string, string> = {
  f1_score: 'F1',
  accuracy: '准确率',
  precision: '精确率',
  recall: '召回率',
  cv_f1_mean: 'CV-F1',
}

/** 格式化单个指标值：0-1 之间按百分比显示，其余保留原值 */
function formatMetric(v: unknown): string {
  if (typeof v === 'number' && Number.isFinite(v)) {
    return v >= 0 && v <= 1 ? `${(v * 100).toFixed(1)}%` : String(v)
  }
  return String(v)
}

/** 归一化模型 metrics 为 [{label, value}] 列表 */
function metricEntries(metrics?: Record<string, unknown>): Array<{ key: string; label: string; value: string }> {
  if (!metrics) return []
  const keys = [
    ...Object.keys(METRIC_LABELS).filter(k => k in metrics),
    ...Object.keys(metrics).filter(k => !(k in METRIC_LABELS)),
  ]
  return keys.map(k => ({ key: k, label: METRIC_LABELS[k] ?? k, value: formatMetric(metrics[k]) }))
}

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

// 终态（completed/failed）后停止轮询但保留面板展示
watch(() => trainStatus.value?.status, (s) => {
  if (s === 'completed' || s === 'failed') pauseTrainPolling()
})

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
  resumeHealthPolling()
  resumeUptimeTick()
  fetchModels()
  getTrainStatus().then(data => {
    if (data && data.status === 'training') {
      trainStatus.value = data
      resumeTrainPolling()
    }
  }).catch(() => {})
  // T3: 从 /api/check 补充 is_admin 和候选网卡数
  getCheck().then(data => {
    if (data?.capture) {
      const cap = data.capture as Record<string, unknown>
      if (captureDiag.value) {
        captureDiag.value.is_admin = cap.is_admin as boolean | undefined
        captureDiag.value.candidate_count = (cap.candidate_ifaces as unknown[])?.length
      } else {
        captureDiag.value = {
          status: cap.ok ? 'available' : 'unavailable',
          is_admin: cap.is_admin as boolean | undefined,
          iface: cap.iface as string | undefined,
          iface_source: cap.iface_source as string | undefined,
          filter: cap.filter as string | undefined,
          candidate_count: (cap.candidate_ifaces as unknown[])?.length,
        }
      }
    }
  }).catch(() => {})
})

onUnmounted(() => {
  pauseTrainPolling()
  pauseHealthPolling()
  pauseUptimeTick()
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
          <el-descriptions-item label="时间戳">{{ formatDatetime(health.timestamp) }}</el-descriptions-item>
          <el-descriptions-item label="运行时间">{{ uptimeText || formatUptime(health.uptime_seconds) }}</el-descriptions-item>
        </el-descriptions>
      </div>
      <el-skeleton v-else-if="healthLoading" :rows="2" animated />
      <el-empty v-else description="无法获取系统状态" />
    </el-card>

    <!-- T3: 抓包诊断卡片 -->
    <el-card shadow="hover" class="system-card">
      <template #header><span>抓包诊断</span></template>
      <div v-if="captureDiag" class="capture-diag">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="抓包状态">
            <el-tag :type="captureDiag.status === 'running' ? 'success' : 'info'" size="small">
              {{ captureDiag.status === 'running' ? '运行中' : captureDiag.status === 'stopped' ? '已停止' : captureDiag.status }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="管理员权限">
            <el-tag v-if="captureDiag.is_admin != null" :type="captureDiag.is_admin ? 'success' : 'warning'" size="small">
              {{ captureDiag.is_admin ? '是' : '否' }}
            </el-tag>
            <span v-else class="text-muted">-</span>
          </el-descriptions-item>
          <el-descriptions-item label="生效网卡" :span="2">
            <code v-if="captureDiag.iface" class="iface-code">{{ captureDiag.iface }}</code>
            <span v-else class="text-muted">未指定</span>
            <el-tag v-if="captureDiag.iface_source" size="small" type="info" effect="plain" style="margin-left: 6px">
              {{ captureDiag.iface_source }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="BPF 过滤" :span="2">
            <code v-if="captureDiag.filter" class="filter-code">{{ captureDiag.filter }}</code>
            <span v-else class="text-muted">无</span>
          </el-descriptions-item>
          <el-descriptions-item label="队列长度">
            {{ captureDiag.queue_size ?? '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="丢弃包数">
            <span :class="{ 'text-danger': (captureDiag.dropped_packets ?? 0) > 0 }">
              {{ captureDiag.dropped_packets ?? 0 }}
            </span>
          </el-descriptions-item>
          <el-descriptions-item label="候选网卡数" v-if="captureDiag.candidate_count != null">
            {{ captureDiag.candidate_count }}
          </el-descriptions-item>
        </el-descriptions>
        <!-- 增强抓包子面板 -->
        <div v-if="captureDiag.enhanced" class="enhanced-section">
          <el-divider content-position="left">增强抓包</el-divider>
          <el-descriptions :column="3" border size="small">
            <el-descriptions-item label="状态">
              <el-tag :type="captureDiag.enhanced.status === 'running' ? 'success' : 'info'" size="small">
                {{ captureDiag.enhanced.status === 'running' ? '运行中' : captureDiag.enhanced.status === 'stopped' ? '已停止' : captureDiag.enhanced.status ?? '-' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="捕获包数">{{ captureDiag.enhanced.packets ?? 0 }}</el-descriptions-item>
            <el-descriptions-item label="流数">{{ captureDiag.enhanced.flows ?? 0 }}</el-descriptions-item>
            <el-descriptions-item v-if="captureDiag.enhanced.error" label="错误" :span="3">
              <span class="text-danger">{{ captureDiag.enhanced.error }}</span>
            </el-descriptions-item>
          </el-descriptions>
        </div>
      </div>
      <el-empty v-else description="暂无抓包诊断数据" />
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
        <el-table-column label="名称" min-width="180">
          <template #default="{ row }">
            <span>{{ row.name }}</span>
            <el-tag v-if="row.is_best" size="small" type="warning" effect="light" style="margin-left: 6px">当前最佳</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="version" label="版本" width="100" />
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">
            {{ formatDatetime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="指标" min-width="220">
          <template #default="{ row }">
            <template v-if="metricEntries(row.metrics).length">
              <el-tag
                v-for="m in metricEntries(row.metrics).slice(0, 4)"
                :key="m.key"
                size="small"
                type="info"
                effect="plain"
                style="margin-right: 6px; margin-bottom: 2px"
              >
                {{ m.label }} {{ m.value }}
              </el-tag>
              <el-tooltip
                v-if="metricEntries(row.metrics).length > 4"
                :content="metricEntries(row.metrics).slice(4).map(m => `${m.label}: ${m.value}`).join('，')"
                placement="top"
              >
                <el-tag size="small" type="info" effect="plain">+{{ metricEntries(row.metrics).length - 4 }}</el-tag>
              </el-tooltip>
            </template>
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
.capture-diag {
  margin-bottom: 8px;
}
.enhanced-section {
  margin-top: 4px;
}
.iface-code, .filter-code {
  font-family: 'Courier New', monospace;
  font-size: 12px;
  background: var(--el-fill-color-light);
  padding: 2px 6px;
  border-radius: 3px;
  word-break: break-all;
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
  color: var(--el-text-color-primary);
}
.json-summary {
  background: var(--el-fill-color-light);
  padding: 8px 12px;
  border-radius: 4px;
  font-size: 12px;
  overflow-x: auto;
}
</style>