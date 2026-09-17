<script setup lang="ts">
import { onMounted, computed, ref } from 'vue'
import { useTrafficStore } from '@/stores/traffic'
import { useAlertStore } from '@/stores/alert'
import { getTraffic, getAlerts } from '@/api/endpoints'
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

// SSE 实时推送为主，无需轮询兜底（流量数据由 SSE traffic 事件驱动）

// 告警级别过滤
const alertLevelFilter = ref('all')

const filteredAlerts = computed(() => {
  const alerts = alertStore.recentAlerts
  if (alertLevelFilter.value === 'all') return alerts
  return alerts.filter(a => a.severity === alertLevelFilter.value)
})

// 流量图表选项
const trafficChartOption = computed(() => {
  const chartData = trafficStore.chartData
  return {
    title: { text: '流量趋势', left: 'center', textStyle: { fontSize: 14 } },
    tooltip: { trigger: 'axis' },
    legend: { data: ['包/秒', '字节/秒'], top: 30 },
    grid: { left: 60, right: 30, top: 70, bottom: 30 },
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
      },
      {
        name: '字节/秒',
        type: 'line',
        data: chartData.bytesPerSec,
        smooth: true,
        showSymbol: false,
        yAxisIndex: 1,
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
  // 初始加载 — getTraffic 返回格式与 SSE traffic 事件不同，仅用于初始化展示
  getTraffic().then(data => {
    if (data && typeof data === 'object' && 'packets_per_second' in data) {
      // REST API 返回格式适配到 TrafficPoint
      trafficStore.updateTraffic({
        timestamp: new Date().toISOString(),
        packets_per_sec: (data as { packets_per_second: number }).packets_per_second,
        bytes_per_sec: (data as { avg_packet_size: number }).avg_packet_size * (data as { packets_per_second: number }).packets_per_second,
        active_flows: 0,
      })
    }
  }).catch(() => {})
  getAlerts({ limit: 50 }).then(data => {
    if (Array.isArray(data)) {
      data.forEach(a => alertStore.addAlert(a))
    }
  }).catch(() => {})
})
</script>

<template>
  <div class="observe-view">
    <!-- 指标卡片 -->
    <el-row :gutter="16" class="metric-cards">
      <el-col :span="6">
        <el-card shadow="hover" class="metric-card">
          <div class="metric-value">{{ trafficStore.packetsPerSec }}</div>
          <div class="metric-label">包/秒</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="metric-card">
          <div class="metric-value">{{ formatBytes(trafficStore.bytesPerSec) }}</div>
          <div class="metric-label">字节/秒</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="metric-card">
          <div class="metric-value">{{ trafficStore.activeFlows }}</div>
          <div class="metric-label">活跃流</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="metric-card">
          <div class="metric-value" :class="{ 'text-danger': alertStore.activeAlertCount > 0 }">
            {{ alertStore.activeAlertCount }}
          </div>
          <div class="metric-label">活跃告警</div>
        </el-card>
      </el-col>
    </el-row>

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

      <el-table :data="filteredAlerts.slice().reverse()" stripe max-height="400" empty-text="暂无告警">
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
.metric-card {
  text-align: center;
  padding: 8px 0;
}
.metric-value {
  font-size: 28px;
  font-weight: 600;
  color: #303133;
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