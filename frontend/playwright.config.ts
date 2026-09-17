/**
 * Playwright 配置 — T3.10 浏览器真机冒烟。
 *
 * ⚠️ 不用 playwright webServer：Windows 上收尾杀不掉 python 子进程会把整个 run 挂死，
 * 且用例③需要测试中途真杀后端再拉起。服务进程由 e2e/helpers/server.ts 自管：
 * globalSetup 拉起 → 用例③可 killServer/startServer → globalTeardown 强杀进程树。
 * workers=1：限流/CSRF/服务生命周期用例依赖全局状态，必须串行。
 */
import { defineConfig } from '@playwright/test'

const PORT = 8898

export default defineConfig({
  globalSetup: './e2e/global-setup.ts',
  globalTeardown: './e2e/global-teardown.ts',
  testDir: './e2e',
  timeout: 90_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    viewport: { width: 1440, height: 900 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  expect: { timeout: 15_000 },
})
