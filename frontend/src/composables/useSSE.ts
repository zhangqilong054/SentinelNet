/**
 * SSE composable — 单条 SSE 连接 + 断线重连 + 订阅分发。
 *
 * T3.4 核心实现，替代旧前端 2 条 EventSource + 8 个 setInterval。
 * 使用 @vueuse/core 的 useEventSource（内置断线重连+状态机）。
 *
 * SSE endpoint: /stream?topics=alert,traffic
 * 注意：topic 用单数（alert/traffic），非复数，这是 T2.9 修复后的契约。
 */

import { useEventSource } from '@vueuse/core'
import { watch, type Ref } from 'vue'
import { useAlertStore } from '@/stores/alert'
import { useTrafficStore } from '@/stores/traffic'
import { useSseStore } from '@/stores/sse'

/** SSE 事件数据格式 — 后端 event 字段名即 topic */
interface SseEventData {
  topic?: string
  data?: unknown
  [key: string]: unknown
}

/**
 * 使用 SSE 连接。
 * @param enabled - 响应式布尔值，控制连接的开启/关闭
 */
export function useSSE(enabled: Ref<boolean>) {
  const sseStore = useSseStore()
  const alertStore = useAlertStore()
  const trafficStore = useTrafficStore()

  // 单条 SSE 连接：/stream?topics=alert,traffic
  // useEventSource 内置断线重连
  const { status, data, error, close, open } = useEventSource(
    '/stream?topics=alert,traffic',
    [],  // 无自定义事件名，使用默认 message
    { autoReconnect: { retries: 10, delay: 3000 } },
  )

  // 监听连接状态
  watch(status, (newStatus) => {
    if (newStatus === 'OPEN') {
      sseStore.setConnected(true)
    } else if (newStatus === 'CONNECTING') {
      sseStore.setReconnecting(true)
    } else {
      sseStore.setConnected(false)
    }
  })

  // 监听错误
  watch(error, (err) => {
    if (err) {
      console.error('[SSE] 连接错误:', err)
      sseStore.setConnected(false)
    }
  })

  // 分发 SSE 事件到对应 store
  watch(data, (rawData) => {
    if (!rawData) return
    try {
      const event: SseEventData = JSON.parse(rawData)
      sseStore.incrementEventCount()
      dispatchEvent(event)
    } catch {
      // 非JSON数据（如connected帧），忽略
      console.debug('[SSE] 非JSON事件:', rawData)
    }
  })

  /** 事件分发 — 根据 topic 路由到对应 store */
  function dispatchEvent(event: SseEventData) {
    // 后端 SSE 格式: event 字段名即 topic
    // 支持两种格式:
    // 1. { topic: "alert", data: {...} }
    // 2. 直接就是数据（topic 从 event name 推导）
    const topic = event.topic
    const payload = event.data ?? event

    switch (topic) {
      case 'alert':
        alertStore.addAlert(payload as Parameters<typeof alertStore.addAlert>[0])
        break
      case 'traffic':
        trafficStore.updateTraffic(payload as Parameters<typeof trafficStore.updateTraffic>[0])
        break
      default:
        console.debug('[SSE] 未知 topic:', topic)
    }
  }

  // 受 enabled 控制
  watch(enabled, (isEnabled) => {
    if (isEnabled) {
      open()
    } else {
      close()
    }
  })

  return {
    status,
    close,
    open,
  }
}