import { defineStore } from 'pinia'
import { ref } from 'vue'

/** 阈值配置 — 对应旧前端 controls.js 中的 6 类阈值 */
export interface ThresholdConfig {
  ddos: number
  port_scan: number
  syn_flood: number
  udp_flood: number
  brute_force: number
  lateral: number
}

/** 设置 store — 管理阈值配置与系统设置 */
export const useSettingsStore = defineStore('settings', () => {
  // ── 状态 ──────────────────────────────────────────────
  const thresholds = ref<ThresholdConfig>({
    ddos: 100,
    port_scan: 100,
    syn_flood: 100,
    udp_flood: 100,
    brute_force: 100,
    lateral: 100,
  })

  const loading = ref(false)
  const error = ref<string | null>(null)

  // ── 动作 ──────────────────────────────────────────────
  function setThresholds(newThresholds: ThresholdConfig) {
    thresholds.value = { ...newThresholds }
  }

  function updateThreshold(key: keyof ThresholdConfig, value: number) {
    thresholds.value[key] = value
  }

  function clearError() {
    error.value = null
  }

  return {
    thresholds,
    loading,
    error,
    setThresholds,
    updateThreshold,
    clearError,
  }
})