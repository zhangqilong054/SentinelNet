import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

/** 告警 store — 管理实时告警列表与告警冷却状态 */
export const useAlertStore = defineStore('alert', () => {
  // ── 状态 ──────────────────────────────────────────────
  const alerts = ref<AlertItem[]>([])
  const cooldownMap = ref<Record<string, number>>({})

  // ── 计算属性 ──────────────────────────────────────────
  const recentAlerts = computed(() =>
    alerts.value.slice(-50),
  )

  const activeAlertCount = computed(() =>
    alerts.value.filter(a => !a.suppressed).length,
  )

  // ── 动作 ──────────────────────────────────────────────
  function addAlert(alert: AlertItem) {
    alerts.value.push(alert)
    // 保留最近 200 条，避免内存泄漏
    if (alerts.value.length > 200) {
      alerts.value = alerts.value.slice(-200)
    }
  }

  function clearAlerts() {
    alerts.value = []
  }

  return {
    alerts,
    cooldownMap,
    recentAlerts,
    activeAlertCount,
    addAlert,
    clearAlerts,
  }
})

/** 告警项类型（T3.2 会从 OpenAPI spec 自动生成） */
export interface AlertItem {
  id: string
  timestamp: string
  alert_type: string
  severity: string
  src_ip: string
  dst_ip: string
  message: string
  suppressed?: boolean
}