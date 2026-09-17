import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

/** SSE 事件 store — 管理单条 SSE 连接状态与事件分发 */
export const useSseStore = defineStore('sse', () => {
  // ── 状态 ──────────────────────────────────────────────
  const connected = ref(false)
  const reconnecting = ref(false)
  const lastEventId = ref<string | null>(null)
  const eventCount = ref(0)

  // ── 计算属性 ──────────────────────────────────────────
  const status = computed(() => {
    if (connected.value) return 'connected'
    if (reconnecting.value) return 'reconnecting'
    return 'disconnected'
  })

  const statusText = computed(() => {
    switch (status.value) {
      case 'connected': return '已连接'
      case 'reconnecting': return '重连中...'
      default: return '未连接'
    }
  })

  // ── 动作 ──────────────────────────────────────────────
  function setConnected(value: boolean) {
    connected.value = value
    if (value) {
      reconnecting.value = false
    }
  }

  function setReconnecting(value: boolean) {
    reconnecting.value = value
    if (value) {
      connected.value = false
    }
  }

  function incrementEventCount() {
    eventCount.value++
  }

  function reset() {
    connected.value = false
    reconnecting.value = false
    lastEventId.value = null
    eventCount.value = 0
  }

  return {
    connected,
    reconnecting,
    lastEventId,
    eventCount,
    status,
    statusText,
    setConnected,
    setReconnecting,
    incrementEventCount,
    reset,
  }
})