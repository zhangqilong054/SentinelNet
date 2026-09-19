/**
 * 主题管理 — 暗色模式开关（2026-09-19 UI 优化）。
 *
 * 机制：Element Plus 的暗色变量由 `html.dark` 类驱动（main.ts 已引入
 * dark/css-vars.css），此处只负责类切换 + localStorage 持久化。
 * 视图内的硬编码浅色背景应改用 var(--el-bg-color-page) / var(--el-fill-color-*)
 * 等语义变量，两种主题下自动适配。
 */

const STORAGE_KEY = 'sn-theme'

/** 应用启动时恢复上次选择的主题 */
export function initTheme(): void {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved === 'dark') {
    document.documentElement.classList.add('dark')
  }
}

/** 当前是否暗色 */
export function isDark(): boolean {
  return document.documentElement.classList.contains('dark')
}

/** 切换暗色模式并持久化 */
export function toggleTheme(): boolean {
  const dark = !isDark()
  document.documentElement.classList.toggle('dark', dark)
  localStorage.setItem(STORAGE_KEY, dark ? 'dark' : 'light')
  return dark
}
