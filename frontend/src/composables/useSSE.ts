/**
 * SSE composable — 单条 SSE 连接 + 断线重连 + 订阅分发。
 *
 * T3.4 核心实现，替代旧前端 2 条 EventSource + 8 个 setInterval。
 * 使用 @vueuse/core 的 useEventSource（内置断线重连+状态机）。
 *
 * SSE endpoint: /api/stream?topics=alert,traffic（注意：后端唯一端点带 /api 前缀）
 * 帧契约（唯一真相源 runtime/events.py + api/stream.py）：
 *   event: alert     data = 载荷本身（无 {topic,data} 包装）
 *   event: traffic   data = 载荷本身
 *   event: connected data = { topics: [...] }
 *   event: error     data = { error: ... }
 * 注意：topic 用单数（alert/traffic），非复数，这是 T2.9 修复后的契约。
 * T3 审计修复（P0-2）：原实现连 /stream 且只监听默认 message 事件，
 * 后端命名帧一条都收不到。
 */

import { useEventSource } from '@vueuse/core'
import { watch, type Ref } from 'vue'
import { useAlertStore } from '@/stores/alert'
import { useTrafficStore } from '@/stores/traffic'
import { useSseStore } from '@/stores/sse'

/** SSE 帧事件名 — 与后端 TOPIC_* 契约一致（含连接/错误帧） */
type SseEventName = 'alert' | 'traffic' | 'connected' | 'error'

const SSE_URL = '/api/stream?topics=alert,traffic'
const SSE_EVENTS: SseEventName[] = ['alert', 'traffic', 'connected', 'error']

/**
 * 使用 SSE 连接。
 * @param enabled - 响应式布尔值，控制连接的开启/关闭
 */
export function useSSE(enabled: Ref<boolean>) {
  const sseStore = useSseStore()
  const alertStore = useAlertStore()
  const trafficStore = useTrafficStore()

  // 单条 SSE 连接，监听后端全部命名帧（默认 message 事件收不到命名帧）
  // useEventSource 内置断线重连；event ref 携带最新帧的事件名
  const { status, event, data, error, close, open } = useEventSource(
    SSE_URL,
    SSE_EVENTS,
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

  // 分发 SSE 命名帧到对应 store（帧名即 topic）
  watch([event, data], ([eventName, rawData]) => {
    if (!eventName || rawData === null || rawData === undefined) return
    sseStore.incrementEventCount()
    dispatchEvent(eventName as SseEventName, rawData)
  })

  /** 事件分发 — 根据帧名路由到对应 store，data 即载荷本身 */
  function dispatchEvent(eventName: SseEventName, rawData: unknown) {
    switch (eventName) {
      case 'alert':
        alertStore.addAlert(rawData as Parameters<typeof alertStore.addAlert>[0])
        break
      case 'traffic':
        trafficStore.updateTraffic(rawData as Parameters<typeof trafficStore.updateTraffic>[0])
        break
      case 'connected':
        sseStore.setConnected(true)
        break
      case 'error':
        console.error('[SSE] 服务端错误帧:', rawData)
        break
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
