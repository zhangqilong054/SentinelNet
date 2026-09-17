/**
 * API 类型定义 — T3.2 会用 openapi-typescript 从 OpenAPI spec 自动生成。
 * 此处仅定义骨架，供 T3.1 编译通过。
 */

/** 健康检查响应 */
export interface HealthResponse {
  status: string
  version: string
  uptime: number
  model_loaded: boolean
}

/** 任务列表响应 */
export interface TaskListResponse {
  tasks: Task[]
}

export interface Task {
  id: string
  name: string
  status: string
  progress?: number
  created_at: string
  started_at?: string
  finished_at?: string
}

/** 剧本列表响应 */
export interface ScenarioListResponse {
  scenarios: Scenario[]
}

export interface Scenario {
  id: string
  name: string
  description: string
}