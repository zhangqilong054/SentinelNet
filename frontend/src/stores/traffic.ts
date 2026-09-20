import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

/** 流量数据点 — SSE 推送的 traffic 事件格式 */
export interface TrafficPoint {
  timestamp: string
  packets_per_sec: number
  bytes_per_sec: number
  active_flows: number
  protocol_breakdown?: Record<string, number>
  data_source?: string  // T2: "capture"=真实抓包 / "demo"=模拟兜底
}

/** 流量 store — 管理实时流量数据与历史趋势 */
export const useTrafficStore = defineStore('traffic', () => {
  // ── 状态 ──────────────────────────────────────────────
  const current = ref<TrafficPoint | null>(null)
  const history = ref<TrafficPoint[]>([])
  const maxHistoryLength = 300  // 保留最近 300 个点（约 5 分钟 @ 1s/点）
  /** T2: 数据来源 — "capture"=真实抓包 / "demo"=模拟兜底 */
  const dataSource = ref<string>('demo')

  // ── 计算属性 ──────────────────────────────────────────
  const packetsPerSec = computed(() => current.value?.packets_per_sec ?? 0)
  const bytesPerSec = computed(() => current.value?.bytes_per_sec ?? 0)
  const activeFlows = computed(() => current.value?.active_flows ?? 0)

  /** ECharts 友好的时间序列数据 */
  const chartData = computed(() => ({
    timestamps: history.value.map(p => p.timestamp),
    packetsPerSec: history.value.map(p => p.packets_per_sec),
    bytesPerSec: history.value.map(p => p.bytes_per_sec),
  }))

  // ── 动作 ──────────────────────────────────────────────
  function updateTraffic(point: TrafficPoint) {
    current.value = point
    history.value.push(point)
    // 保留最近 maxHistoryLength 个点
    if (history.value.length > maxHistoryLength) {
      history.value = history.value.slice(-maxHistoryLength)
    }
    // T2: 更新数据来源标记
    if (point.data_source) {
      dataSource.value = point.data_source
    }
  }

  function clearHistory() {
    history.value = []
    current.value = null
  }

  return {
    current,
    history,
    packetsPerSec,
    bytesPerSec,
    activeFlows,
    chartData,
    dataSource,
    updateTraffic,
    clearHistory,
  }
})