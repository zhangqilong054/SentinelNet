<script setup lang="ts">
import { onMounted, onUnmounted, computed, ref, watch } from 'vue'
import { useTrafficStore } from '@/stores/traffic'
import { useAlertStore } from '@/stores/alert'
import { getTraffic, getAlerts, getHealth } from '@/api/endpoints'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import {
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

use([LineChart, TitleComponent, TooltipComponent, LegendComponent, GridComponent, CanvasRenderer])

const trafficStore = useTrafficStore()
const alertStore = useAlertStore()

// ── M1.2: 抓包状态徽标条 ───────────────────────────────────
interface CaptureStatus {
  status: string
  iface: string | null
  iface_source: string
  filter: string
  resolved: boolean
  dropped_packets: number
  queue_size: number
  queue_capacity: number
  queue_usage: number
  backlog_level: 'ok' | 'warn' | 'critical'
}
const captureStatus = ref<CaptureStatus | null>(null)
let healthTimer: ReturnType<typeof setInterval> | null = null

async function pollCaptureStatus() {
  try {
    const data = await getHealth() as { components?: { capture?: CaptureStatus } }
    captureStatus.value = data.components?.capture ?? null
  } catch {
    // 轮询失败不影响主界面
  }
}

// M1.2: iface_source 徽标颜色
function ifaceSourceColor(source: string): string {
  switch (source) {
    case 'config': return '#67c23a'       // 绿 — 用户显式配置
    case 'auto:route': return '#409eff'   // 蓝 — 默认路由自动识别
    case 'auto:active': return '#e6a23c'  // 橙 — 活动网卡打分
    case 'fallback': return '#909399'     // 灰 — 兜底（可能 0 包）
    default: return '#909399'
  }
}

// M1.2: iface_source 显示文本
function ifaceSourceLabel(source: string): string {
  switch (source) {
    case 'config': return '配置'
    case 'auto:route': return '路由'
    case 'auto:active': return '活动'
    case 'fallback': return '兜底'
    case 'unset': return '未设置'
    default: return source
  }
}

// M1.3: 积压/丢包警示
const prevDropped = ref(0)
const droppedDelta = computed(() => {
  if (!captureStatus.value) return 0
  const delta = captureStatus.value.dropped_packets - prevDropped.value
  return delta > 0 ? delta : 0
})
watch(captureStatus, (val) => {
  if (val && val.dropped_packets > prevDropped.value) {
    prevDropped.value = val.dropped_packets
  }
})

// SSE 实时推送为主，无需轮询兜底（流量数据由 SSE traffic 事件驱动）

// 告警级别过滤
const alertLevelFilter = ref('all')

// ── 新告警高亮动效 ────────────────────────────────────
// 首载 REST 告警视为「已读」不触发；此后 SSE 新推的告警 5s 内高亮渐隐。
const seenAlertIds = new Set<string>()
const newAlertIds = ref(new Set<string>())
let flashTimers: ReturnType<typeof setTimeout>[] = []
let bootstrapping = true // 首载 REST 告警视为已读

watch(() => alertStore.alerts.length, (newLen, oldLen) => {
  if (oldLen === undefined) return
  for (let i = Math.max(oldLen, 0); i < newLen; i++) {
    const a = alertStore.alerts[i]
    if (!a || seenAlertIds.has(a.id)) continue
    seenAlertIds.add(a.id)
    if (bootstrapping) continue
    newAlertIds.value.add(a.id)
    flashTimers.push(setTimeout(() => {
      newAlertIds.value.delete(a.id)
      // 触发 Set 的响应式更新
      newAlertIds.value = new Set(newAlertIds.value)
    }, 5000))
  }
})

function alertRowClass({ row }: { row: { id: string } }): string {
  return newAlertIds.value.has(row.id) ? 'alert-row-new' : ''
}

onUnmounted(() => {
  flashTimers.forEach(clearTimeout)
  if (healthTimer) {
    clearInterval(healthTimer)
    healthTimer = null
  }
})

const filteredAlerts = computed(() => {
  const alerts = alertStore.recentAlerts
  if (alertLevelFilter.value === 'all') return alerts
  return alerts.filter(a => a.severity === alertLevelFilter.value)
})

// 流量图表选项 — M1.1: 新增包速率曲线
const trafficChartOption = computed(() => {
  const chartData = trafficStore.chartData
  const isDemo = trafficStore.dataSource === 'demo'
  // M1.1: demo 模式下曲线降透明度
  const seriesOpacity = isDemo ? 0.45 : 1
  return {
    title: { text: '流量趋势', left: 'center', textStyle: { fontSize: 14 } },
    tooltip: { trigger: 'axis' },
    legend: { data: ['包/秒', '字节/秒', '包速率'], top: 30 },
    grid: { left: 60, right: 60, top: 70, bottom: 30 },
    xAxis: {
      type: 'category',
      data: chartData.timestamps.map(t => {
        try {
          return new Date(t).toLocaleTimeString('zh-CN', { hour12: false })
        } catch {
          return t
        }
      }),
      axisLabel: { fontSize: 10, rotate: 30 },
    },
    yAxis: [
      { type: 'value', name: '包/秒', position: 'left' },
      { type: 'value', name: '字节/秒', position: 'right' },
    ],
    series: [
      {
        name: '包/秒',
        type: 'line',
        data: chartData.packetsPerSec,
        smooth: true,
        showSymbol: false,
        yAxisIndex: 0,
        itemStyle: { opacity: seriesOpacity },
        lineStyle: { opacity: seriesOpacity },
      },
      {
        name: '字节/秒',
        type: 'line',
        data: chartData.bytesPerSec,
        smooth: true,
        showSymbol: false,
        yAxisIndex: 1,
        itemStyle: { opacity: seriesOpacity },
        lineStyle: { opacity: seriesOpacity },
      },
      {
        name: '包速率',
        type: 'line',
        data: chartData.packetRates,
        smooth: true,
        showSymbol: false,
        yAxisIndex: 0,
        lineStyle: { type: 'dashed', opacity: seriesOpacity },
        itemStyle: { opacity: seriesOpacity, color: '#e6a23c' },
      },
    ],
  }
})

// 格式化字节
function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`
}

// 告警级别颜色
function severityType(severity: string): 'danger' | 'warning' | 'info' {
  switch (severity) {
    case 'critical': return 'danger'
    case 'high': return 'danger'
    case 'medium': return 'warning'
    case 'low': return 'info'
    default: return 'info'
  }
}

function severityLabel(severity: string): string {
  switch (severity) {
    case 'critical': return '严重'
    case 'high': return '高'
    case 'medium': return '中'
    case 'low': return '低'
    default: return severity
  }
}

onMounted(() => {
  // 初始加载 — REST 契约 TrafficStatsResponse：{ qps, connections, data_source, ... }，与 SSE traffic 帧字段不同
  getTraffic().then(data => {
    if (data && typeof data === 'object' && 'qps' in data) {
      const stats = data as { qps: number; connections: number; data_source?: string; packet_count?: number }
      trafficStore.updateTraffic({
        timestamp: new Date().toISOString(),
        packets_per_sec: stats.qps,
        bytes_per_sec: 0,
        active_flows: stats.connections,
        data_source: stats.data_source,
        packet_count: stats.packet_count,
      })
    }
  }).catch(() => {})
  getAlerts({ limit: 50 }).then(data => {
    if (Array.isArray(data)) {
      data.forEach(a => alertStore.addAlert(a))
    }
  }).catch(() => {}).finally(() => {
    bootstrapping = false
  })
  // M1.2: 轮询抓包状态（5s 间隔）
  pollCaptureStatus()
  healthTimer = setInterval(pollCaptureStatus, 5000)
})
</script>

<template>
  <div class="observe-view">
    <!-- 指标卡片（xs 半宽 / sm+ 四分之一宽） -->
    <el-row :gutter="16" class="metric-cards">
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="metric-card">
          <div class="metric-value">{{ trafficStore.packetsPerSec }}</div>
          <div class="metric-label">包/秒</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="metric-card">
          <div class="metric-value">{{ formatBytes(trafficStore.bytesPerSec) }}</div>
          <div class="metric-label">字节/秒</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="metric-card">
          <div class="metric-value">{{ trafficStore.activeFlows }}</div>
          <div class="metric-label">活跃流</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="metric-card">
          <div class="metric-value" :class="{ 'text-danger': alertStore.activeAlertCount > 0 }">
            {{ alertStore.activeAlertCount }}
          </div>
          <div class="metric-label">活跃告警</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- T2: 模拟数据水印 — data_source 为 demo 时显示 -->
    <el-alert
      v-if="trafficStore.dataSource === 'demo'"
      type="warning"
      :closable="false"
      show-icon
      class="demo-watermark"
    >
      <template #title>
        <span>当前显示模拟数据（未启动抓包或无真实流量）</span>
      </template>
    </el-alert>

    <!-- M1.2: 抓包状态徽标条 -->
    <el-card v-if="captureStatus" shadow="never" class="capture-badge-bar">
      <div class="badge-row">
        <span class="badge-label">抓包</span>
        <el-tag :type="captureStatus.status === 'running' ? 'success' : 'info'" size="small" effect="dark">
          {{ captureStatus.status === 'running' ? '运行中' : '已停止' }}
        </el-tag>

        <template v-if="captureStatus.iface">
          <span class="badge-sep">|</span>
          <span class="badge-label">网卡</span>
          <el-tooltip :content="captureStatus.iface" placement="top">
            <span class="iface-name">{{ captureStatus.iface.replace(/\\Device\\NPF_/, '') }}</span>
          </el-tooltip>
          <el-tag
            size="small"
            :color="ifaceSourceColor(captureStatus.iface_source)"
            effect="dark"
            style="color: #fff; border: none;"
          >
            {{ ifaceSourceLabel(captureStatus.iface_source) }}
          </el-tag>
        </template>

        <template v-if="!captureStatus.resolved">
          <el-tag type="info" size="small" effect="plain">预览</el-tag>
        </template>

        <template v-if="captureStatus.filter">
          <span class="badge-sep">|</span>
          <span class="badge-label">BPF</span>
          <el-tooltip :content="captureStatus.filter" placement="top">
            <span class="bpf-text">{{ captureStatus.filter.length > 30 ? captureStatus.filter.slice(0, 30) + '…' : captureStatus.filter }}</span>
          </el-tooltip>
        </template>

        <!-- M1.3: 积压水位 -->
        <template v-if="captureStatus.backlog_level !== 'ok'">
          <span class="badge-sep">|</span>
          <el-tag
            :type="captureStatus.backlog_level === 'critical' ? 'danger' : 'warning'"
            size="small"
            effect="dark"
          >
            队列 {{ captureStatus.backlog_level === 'critical' ? '危险' : '警告' }}
            ({{ (captureStatus.queue_usage * 100).toFixed(1) }}%)
          </el-tag>
        </template>

        <!-- M1.3: 丢包计数 -->
        <template v-if="captureStatus.dropped_packets > 0">
          <span class="badge-sep">|</span>
          <el-tag type="danger" size="small" effect="dark">
            丢包 {{ captureStatus.dropped_packets }}
          </el-tag>
        </template>
      </div>
    </el-card>

    <!-- 流量图表 -->
    <el-card shadow="hover" class="chart-card">
      <v-chart
        :option="trafficChartOption"
        :autoresize="true"
        style="height: 320px; width: 100%"
      />
    </el-card>

    <!-- 告警列表 -->
    <el-card shadow="hover" class="alert-card">
      <template #header>
        <div class="alert-header">
          <span>最近告警</span>
          <el-radio-group v-model="alertLevelFilter" size="small">
            <el-radio-button value="all">全部</el-radio-button>
            <el-radio-button value="critical">严重</el-radio-button>
            <el-radio-button value="high">高</el-radio-button>
            <el-radio-button value="medium">中</el-radio-button>
            <el-radio-button value="low">低</el-radio-button>
          </el-radio-group>
        </div>
      </template>

      <el-table :data="filteredAlerts.slice().reverse()" stripe max-height="400" empty-text="暂无告警" :row-class-name="alertRowClass">
        <el-table-column label="时间" width="170">
          <template #default="{ row }">
            {{ new Date(row.timestamp).toLocaleString('zh-CN', { hour12: false }) }}
          </template>
        </el-table-column>
        <el-table-column label="级别" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="severityType(row.severity)" size="small">
              {{ severityLabel(row.severity) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="alert_type" label="类型" width="140" />
        <el-table-column label="源→目标" width="200">
          <template #default="{ row }">
            <span class="ip-flow">{{ row.src_ip }} → {{ row.dst_ip }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="message" label="信息" min-width="200" show-overflow-tooltip />
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.observe-view {
  max-width: 1200px;
}
.metric-cards {
  margin-bottom: 16px;
}
.demo-watermark {
  margin-bottom: 16px;
}
.capture-badge-bar {
  margin-bottom: 16px;
  padding: 4px 0;
}
.capture-badge-bar :deep(.el-card__body) {
  padding: 8px 16px;
}
.badge-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.badge-label {
  font-size: 12px;
  color: #909399;
  font-weight: 500;
}
.badge-sep {
  color: #dcdfe6;
  margin: 0 2px;
}
.iface-name {
  font-family: 'Courier New', monospace;
  font-size: 12px;
  color: var(--el-text-color-primary);
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bpf-text {
  font-family: 'Courier New', monospace;
  font-size: 11px;
  color: #606266;
}
.metric-card {
  text-align: center;
  padding: 8px 0;
}
.metric-value {
  font-size: 28px;
  font-weight: 600;
  /* 语义变量：暗色模式下自动切换 */
  color: var(--el-text-color-primary);
  line-height: 1.2;
}
.metric-label {
  font-size: 13px;
  color: #909399;
  margin-top: 4px;
}
.text-danger {
  color: #f56c6c;
}
.chart-card {
  margin-bottom: 16px;
}
.alert-card {
  margin-bottom: 16px;
}

/* 新告警高亮渐隐（5s 内，由 alertRowClass 控制） */
:deep(.alert-row-new) {
  animation: alert-flash 3s ease-out;
}
@keyframes alert-flash {
  0% {
    background-color: var(--el-color-danger-light-7);
  }
  100% {
    background-color: transparent;
  }
}
.alert-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.ip-flow {
  font-family: 'Courier New', monospace;
  font-size: 12px;
  color: #606266;
}
</style>