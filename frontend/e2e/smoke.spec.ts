/**
 * T3.10 浏览器真机冒烟 — Playwright 自动化部分。
 *
 * 对照任务清单 T3.10：
 * ① SPA 五路由可达且挂载          ② SSE 连接状态「已连接」
 * ④ 表单校验 Toast（训练空数据集）
 * ⑤ 载荷送检真实调用              ⑥ 多标签页并发写（CSRF token 覆盖 + 403 重试）
 * ⑦ 限流生效（payload/analyze 30/min → 第 31 次 429）
 * ⑧ 无 CSRF token 的写操作被拒（403）
 * ⑨ 视觉基线截图（5 页面，兼作 T0.4 基线，人工核对清单见 docs）
 * ⑩ SSE 断线重连（真杀后端进程；⚠️ 必须最后跑，见用例内注释）
 *
 * 人工核对项（自动化无法判定）：图表视觉刷新效果、布局对照旧版差异、
 * ElMessage 样式、多标签页人工操作流——见 e2e/人工核对清单.md。
 */
import { test, expect, type Page } from '@playwright/test'
import { killServer, startServer } from './helpers/server'

const ROUTES = [
  { path: '/', name: 'observe' },
  { path: '/tasks', name: 'tasks' },
  { path: '/config', name: 'config' },
  { path: '/system', name: 'system' },
  { path: '/scenarios', name: 'scenarios' },
] as const

/** 等待 SPA 完成首屏渲染（侧边栏可见）。
 *  ⚠️ 不能用 waitForLoadState('networkidle')：SSE 是长连接，networkidle 永远不会到达。 */
async function waitAppReady(page: Page) {
  await expect(page.locator('.el-menu')).toBeVisible()
}

/** 在页面上下文执行与 client.ts 相同的双提交 CSRF 写请求（含 403 重试） */
async function csrfWrite(page: Page, path: string, body: unknown): Promise<number> {
  return page.evaluate(async ({ path, body }) => {
    const doWrite = (token: string) =>
      fetch(path, {
        method: 'PUT',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
        body: JSON.stringify(body),
      })
    const r1 = await fetch('/api/csrf-token', { credentials: 'same-origin' })
    const t1 = ((await r1.json()) as { csrf_token: string }).csrf_token
    let res = await doWrite(t1)
    if (res.status === 403) {
      // client.ts 的重试逻辑：刷新 token 再试一次
      const r2 = await fetch('/api/csrf-token', { credentials: 'same-origin' })
      const t2 = ((await r2.json()) as { csrf_token: string }).csrf_token
      res = await doWrite(t2)
    }
    return res.status
  }, { path, body })
}

test.describe('T3.10 浏览器真机冒烟', () => {
  test('① SPA 五路由可达且挂载', async ({ page }) => {
    for (const { path } of ROUTES) {
      const resp = await page.goto(path)
      expect(resp?.status(), `路由 ${path} 应 200`).toBe(200)
      await expect(page.locator('#app'), `路由 ${path} 应挂载 Vue`).toBeAttached()
      await expect(page.locator('.el-menu'), `路由 ${path} 应有侧边栏`).toBeVisible()
    }
  })

  test('② SSE 连接状态显示「已连接」', async ({ page }) => {
    await page.goto('/')
    // LayoutView 头部 el-tag 渲染 sseStore.statusText
    await expect(
      page.locator('.el-tag', { hasText: '已连接' }),
    ).toBeVisible()
  })

  test('④ 表单校验：训练空数据集 → warning Toast', async ({ page }) => {
    await page.goto('/system')
    await waitAppReady(page)
    await page.getByRole('button', { name: '启动训练' }).click()
    await expect(page.locator('.el-message')).toContainText('请输入数据集路径')
  })

  test('⑤ 载荷送检真实调用（契约字段渲染）', async ({ page }) => {
    await page.goto('/config')
    await waitAppReady(page)
    await page.locator('textarea').fill('GET /admin HTTP/1.1\r\nHost: example.com')
    await page.getByRole('button', { name: '分析载荷' }).click()
    // 修复轮对齐后的契约展示：异常判定 + 载荷长度 + 告警
    await expect(page.getByText('分析结果')).toBeVisible()
    await expect(page.getByText('异常判定')).toBeVisible()
    await expect(page.getByText('载荷长度')).toBeVisible()
  })

  test('⑥ 多标签页并发写：CSRF token 互相覆盖后仍全部成功（403 重试兜底）', async ({ browser }) => {
    const context = await browser.newContext()
    const pageA = await context.newPage()
    const pageB = await context.newPage()
    await pageA.goto('/')
    await pageB.goto('/')

    // 交替写阈值：B 取 token 会覆盖 A 的 cookie，A 的缓存 token 随即失效
    // 客户端 403 → 刷新 token 重试一次，最终应全部 200
    const statuses: number[] = []
    for (let i = 1; i <= 4; i++) {
      statuses.push(await csrfWrite(pageA, '/api/settings', { key: 'ddos_threshold', value: 1000 + i }))
      statuses.push(await csrfWrite(pageB, '/api/settings', { key: 'ddos_threshold', value: 2000 + i }))
    }
    expect(
      statuses.filter((s) => s !== 200),
      `8 次交替写应全部 200（token 值不同允许 403+重试，但最终必须成功），实际: ${statuses}`,
    ).toHaveLength(0)
    await context.close()
  })

  test('⑦ 限流生效：payload/analyze 30/min → 第 31 次 429', async ({ page }) => {
    await page.goto('/')
    const statuses = await page.evaluate(async () => {
      const r = await fetch('/api/csrf-token', { credentials: 'same-origin' })
      const token = ((await r.json()) as { csrf_token: string }).csrf_token
      const out: number[] = []
      for (let i = 0; i < 31; i++) {
        const res = await fetch('/api/payload/analyze', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
          body: JSON.stringify({ payload: 'rate-limit-probe' }),
        })
        out.push(res.status)
        if (res.status === 429) break
      }
      return out
    })
    expect(statuses.at(-1), `前 30 次应全部 200 后触发 429，实际: ${statuses}`).toBe(429)
    expect(statuses.filter((s) => s === 429)).toHaveLength(1)
  })

  test('⑧ 无 CSRF token 的写操作被拒（403）', async ({ page }) => {
    await page.goto('/')
    const status = await page.evaluate(async () => {
      const res = await fetch('/api/settings', {
        method: 'PUT',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: 'ddos_threshold', value: 1 }),
      })
      return res.status
    })
    expect(status, '无 X-CSRFToken 头的写请求应 403').toBe(403)
  })

  test('⑨ 视觉基线截图（5 页面，兼作 T0.4 基线）', async ({ page }) => {
    for (const { path, name } of ROUTES) {
      await page.goto(path)
      await waitAppReady(page)
      await page.waitForTimeout(800) // 图表/骨架稳定
      await expect(page).toHaveScreenshot(`baseline-${name}.png`, {
        animations: 'disabled',
        fullPage: true,
        maxDiffPixelRatio: 0.02,
      })
    }
  })

  // ⚠️ 必须放最后一个：它会杀掉并重启 uvicorn（真断线），
  // 若之后再跑其他用例，全量 run 的 worker 收尾会挂死（Windows + 重启后的 detached 子进程句柄）。
  test('⑩ SSE 断线重连（真杀后端进程 → 拉起 → 自动恢复）', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('.el-tag', { hasText: '已连接' })).toBeVisible()

    // 真断线：强杀 uvicorn（Chromium 的 offline 模拟掐不断已建立的 SSE 连接，keepalive 仍在流动）
    killServer()
    // useEventSource autoReconnect 10 次 × 3s；错误发生后状态机进入重连/断开
    await expect(
      page.locator('.el-tag', { hasText: '重连中' }).or(page.locator('.el-tag', { hasText: '未连接' })),
    ).toBeVisible({ timeout: 30_000 })

    // 服务端恢复：浏览器应自动重连成功（对应风险 R5：页面挂着、后端重启）
    await startServer()
    await expect(page.locator('.el-tag', { hasText: '已连接' })).toBeVisible({
      timeout: 30_000,
    })
    // 收尾必须显式断掉 SSE 重连循环，否则 worker 收尾挂死（Windows/Chromium 句柄）
    await page.context().close()
  })
})
