/**
 * uvicorn 服务进程管理（e2e 专用）。
 *
 * 为什么不用 playwright 的 webServer：
 * 1. Windows 上 playwright 收尾杀不掉 python 子进程，整个 run 会挂死在 teardown；
 * 2. 用例③需要**测试中途杀掉后端**再拉起（真断线 → SSE 自动重连），
 *    webServer 模式做不到。
 *
 * 进程以 detached 启动并写入 PID 文件，teardown 用 taskkill /T /F 强杀进程树。
 */
import { spawnSync, execSync } from 'child_process'
import { writeFileSync, readFileSync, existsSync, mkdtempSync } from 'fs'
import { tmpdir } from 'os'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'

const HERE = dirname(fileURLToPath(import.meta.url)) // .../frontend/e2e/helpers
const PROJECT_ROOT = join(HERE, '..', '..') // D:/18551/AiC/SentinelNet

export const PORT = 8898
export const BASE_URL = `http://127.0.0.1:${PORT}`
const PID_FILE = join(tmpdir(), 'sn-e2e-uvicorn.pid')

export async function isHealthy(timeoutMs = 1500): Promise<boolean> {
  try {
    const ctrl = new AbortController()
    const timer = setTimeout(() => ctrl.abort(), timeoutMs)
    const res = await fetch(`${BASE_URL}/api/health`, { signal: ctrl.signal })
    clearTimeout(timer)
    return res.ok
  } catch {
    return false
  }
}

async function waitForHealth(timeoutMs = 40_000): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await isHealthy()) return
    await new Promise((r) => setTimeout(r, 500))
  }
  throw new Error(`uvicorn(${PORT}) ${timeoutMs}ms 内未就绪`)
}

/** 启动 uvicorn（隔离 data_dir + FRONTEND=new），阻塞至 /api/health 就绪。
 *  ⚠️ 必须经短命 python 启动器 Popen(DETACHED_PROCESS) 拉起：
 *  若由 playwright worker 直接 spawn（即使 detached+unref），
 *  Windows 上 worker 收尾会因遗留句柄挂死 300s（"worker did not exit"）。 */
export async function startServer(): Promise<void> {
  const dataDir = mkdtempSync(join(tmpdir(), 'sn-e2e-data-'))
  const launcher = [
    "import subprocess,sys",
    "p = subprocess.Popen([sys.executable,'-m','uvicorn','campus_ids.web_new.app:create_app','--factory',"
      + `'--port','${PORT}','--log-level','warning'],`,
    `cwd=r'${PROJECT_ROOT}',`,
    "creationflags=0x00000008|0x00000200,",  // DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)",
    "print(p.pid)",
  ].join('\n')
  const res = spawnSync(
    process.env.PY || 'C:/Users/18551/anaconda3/python.exe',
    ['-c', launcher],
    {
      env: {
        ...process.env,
        CAMPUS_IDS_FRONTEND: 'new',
        CAMPUS_IDS_DEBUG: '1',
        CAMPUS_IDS_DATA_DIR: dataDir,
      },
      timeout: 30_000,
      encoding: 'utf8',
    },
  )
  const pid = Number.parseInt((res.stdout || '').trim().split(/\r?\n/).pop() || '0', 10)
  if (!pid) {
    throw new Error(`uvicorn 启动失败: stdout=${res.stdout} stderr=${res.stderr}`)
  }
  writeFileSync(PID_FILE, String(pid))
  await waitForHealth()
}

/** 强杀 uvicorn 进程树（真实断连） */
export function killServer(): void {
  if (!existsSync(PID_FILE)) return
  const pid = readFileSync(PID_FILE, 'utf8').trim()
  try {
    execSync(`taskkill /T /F /PID ${pid}`, { stdio: 'ignore' })
  } catch {
    /* 进程可能已退出 */
  }
}

/** 供 globalTeardown：健康则杀掉，避免遗留进程占端口 */
export async function ensureCleaned(): Promise<void> {
  if (await isHealthy()) killServer()
}
