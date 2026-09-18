/**
 * API 端点封装 — 基于自动生成的 TS 类型（api-generated.d.ts）。
 *
 * 每个函数对应一个后端 API 端点，返回类型由 OpenAPI spec 推导。
 * T3.2 核心：类型安全，编译期捕获参数/响应不匹配。
 */

import { apiGet, apiPost, apiPut, apiDelete } from './client'
import type { paths } from '@/types/api-generated'

// ── 类型导出 ──────────────────────────────────────────────────
/** 从 paths 和 operations 推导的响应类型 */
type OperationResponse<T extends keyof paths, M extends keyof paths[T]> =
  paths[T][M] extends { responses: { 200: { content: { 'application/json': infer R } } } }
    ? R
    : unknown

/** 请求体类型 */
type OperationRequestBody<T extends keyof paths, M extends keyof paths[T]> =
  paths[T][M] extends { requestBody: { content: { 'application/json': infer B } } }
    ? B
    : never

// ── 健康检查 ──────────────────────────────────────────────────
export const getHealth = () =>
  apiGet<OperationResponse<'/api/health', 'get'>>('/api/health')

// ── 配置 ──────────────────────────────────────────────────────
export const getSettings = () =>
  apiGet<OperationResponse<'/api/settings', 'get'>>('/api/settings')

export const updateSettings = (body: OperationRequestBody<'/api/settings', 'put'>) =>
  apiPut<OperationResponse<'/api/settings', 'put'>>('/api/settings', body)

// ── 流量 ──────────────────────────────────────────────────────
export const getTraffic = () =>
  apiGet<OperationResponse<'/api/traffic', 'get'>>('/api/traffic')

export const getTrafficHistory = () =>
  apiGet<OperationResponse<'/api/traffic/history', 'get'>>('/api/traffic/history')

// ── 告警 ──────────────────────────────────────────────────────
export const getAlerts = (params?: { level?: string; limit?: number; offset?: number }) => {
  const query = new URLSearchParams()
  if (params?.level) query.set('level', params.level)
  if (params?.limit) query.set('limit', String(params.limit))
  if (params?.offset) query.set('offset', String(params.offset))
  const qs = query.toString()
  return apiGet<OperationResponse<'/api/alerts', 'get'>>(qs ? `/api/alerts?${qs}` : '/api/alerts')
}

export const getAlertStats = () =>
  apiGet<OperationResponse<'/api/alerts/stats', 'get'>>('/api/alerts/stats')

// ── 任务 ──────────────────────────────────────────────────────
export const getTasks = () =>
  apiGet<OperationResponse<'/api/tasks', 'get'>>('/api/tasks')

export const startTask = (name: string) =>
  apiPost<OperationResponse<'/api/tasks/{name}/start', 'post'>>(`/api/tasks/${name}/start`)

export const stopTask = (name: string) =>
  apiPost<OperationResponse<'/api/tasks/{name}/stop', 'post'>>(`/api/tasks/${name}/stop`)

// ── 模型 ──────────────────────────────────────────────────────
export const getModels = () =>
  apiGet<OperationResponse<'/api/models', 'get'>>('/api/models')

export const trainModel = (body?: OperationRequestBody<'/api/models/train', 'post'>) =>
  apiPost<OperationResponse<'/api/models/train', 'post'>>('/api/models/train', body)

export const getTrainStatus = () =>
  apiGet<OperationResponse<'/api/models/train/status', 'get'>>('/api/models/train/status')

export const deleteModel = (name: string) =>
  apiDelete<OperationResponse<'/api/models/{name}', 'delete'>>(`/api/models/${name}`)

// ── 剧本 ──────────────────────────────────────────────────────
export const getScenarios = () =>
  apiGet<OperationResponse<'/api/scenarios', 'get'>>('/api/scenarios')

export const startScenario = (body: OperationRequestBody<'/api/scenarios/start', 'post'>) =>
  apiPost<OperationResponse<'/api/scenarios/start', 'post'>>('/api/scenarios/start', body)

export const stopScenario = () =>
  apiPost<OperationResponse<'/api/scenarios/stop', 'post'>>('/api/scenarios/stop')

// ── TLS 分析 ──────────────────────────────────────────────────
export const analyzeTls = () =>
  apiGet<OperationResponse<'/api/tls/analyze', 'get'>>('/api/tls/analyze')

// ── 载荷分析 ──────────────────────────────────────────────────
export const analyzePayload = (body: OperationRequestBody<'/api/payload/analyze', 'post'>) =>
  apiPost<OperationResponse<'/api/payload/analyze', 'post'>>('/api/payload/analyze', body)