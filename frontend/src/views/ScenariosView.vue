<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { getScenarios, startScenario, stopScenario, getTasks } from '@/api/endpoints'
import { ElMessage, ElMessageBox } from 'element-plus'

interface ScenarioInfo {
  name: string
  description: string
  steps: string[]
}

const scenarios = ref<ScenarioInfo[]>([])
const loading = ref(false)
const runningScenario = ref<string | null>(null)
const operating = ref(false)

async function fetchScenarios() {
  loading.value = true
  try {
    const data = await getScenarios()
    if (data) {
      // 支持 { scenarios: [...] } 或直接数组
      scenarios.value = Array.isArray(data) ? data : (data as { scenarios: ScenarioInfo[] }).scenarios ?? []
    }
    await inferRunningFromTasks()
  } catch {
    ElMessage.error('加载剧本列表失败')
  } finally {
    loading.value = false
  }
}

/** 后端无剧本状态端点：用 /api/tasks 推断 —— 任一子任务运行中即视为剧本运行中 */
async function inferRunningFromTasks() {
  try {
    const data = await getTasks()
    const tasks = Array.isArray(data) ? data : (data as { tasks: Array<{ name: string; status: string }> }).tasks ?? []
    const running = new Set(tasks.filter(t => t.status === 'running' || t.status === 'stopping').map(t => t.name))
    const found = scenarios.value.find(s => s.steps.some(step => running.has(step)))
    runningScenario.value = found ? found.name : null
  } catch {
    /* 推断失败不影响主流程 */
  }
}

function scenarioSteps(name: string): string[] {
  return scenarios.value.find(s => s.name === name)?.steps ?? []
}

async function handleStart(name: string) {
  const steps = scenarioSteps(name)
  // dangerouslyUseHTMLString：steps 为后端内部任务名常量，无注入风险；
  // 纯文本拼接长行是「确认界面显示不全」的根因之一（无换行无结构）
  const stepHtml = steps.map(s => `<li>${s}</li>`).join('')
  try {
    await ElMessageBox.confirm(
      `<p>确定启动剧本 <b>${name}</b>？将按序执行以下 ${steps.length} 个子任务：</p><ul style="margin:8px 0 0;padding-left:20px;">${stepHtml}</ul>`,
      '启动确认',
      {
        type: 'info',
        confirmButtonText: '启动',
        cancelButtonText: '取消',
        dangerouslyUseHTMLString: true,
      },
    )
  } catch {
    return
  }

  operating.value = true
  try {
    await startScenario({ scenario: name })
    runningScenario.value = name
    ElMessage.success(`剧本 "${name}" 已启动`)
  } catch (e) {
    const msg = e instanceof Error ? e.message : `启动剧本 "${name}" 失败`
    ElMessage.error(msg)
  } finally {
    operating.value = false
  }
}

async function handleStop() {
  const name = runningScenario.value
  if (!name) {
    ElMessage.info('当前没有运行中的剧本')
    return
  }
  try {
    await ElMessageBox.confirm(`确定停止剧本 "${name}"？（将停止其全部子任务）`, '停止确认', {
      type: 'warning',
      confirmButtonText: '停止',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }

  operating.value = true
  try {
    // 后端 ScenarioStopRequest 必选 body：必须带 scenario 字段
    await stopScenario(name)
    runningScenario.value = null
    ElMessage.success('剧本已停止')
  } catch (e) {
    const msg = e instanceof Error ? e.message : '停止剧本失败'
    ElMessage.error(msg)
  } finally {
    operating.value = false
  }
}

// 剧本图标颜色
function scenarioColor(name: string): string {
  switch (name) {
    case 'demo': return '#409eff'
    case 'full': return '#67c23a'
    case 'attack': return '#f56c6c'
    default: return '#909399'
  }
}

onMounted(fetchScenarios)
</script>

<template>
  <div class="scenarios-view">
    <el-card shadow="hover">
      <template #header>
        <div class="card-header">
          <span>剧本中心</span>
          <el-button size="small" @click="fetchScenarios" :loading="loading">刷新</el-button>
        </div>
      </template>

      <el-skeleton :loading="loading" :rows="4" animated>
        <div class="scenario-grid">
          <el-card
            v-for="s in scenarios"
            :key="s.name"
            shadow="hover"
            class="scenario-card"
            :class="{ 'is-running': runningScenario === s.name }"
          >
            <div class="scenario-icon" :style="{ backgroundColor: scenarioColor(s.name) }">
              {{ s.name.charAt(0).toUpperCase() }}
            </div>
            <h3 class="scenario-name">{{ s.name }}</h3>
            <p class="scenario-desc">{{ s.description }}</p>

            <div class="scenario-tasks">
              <el-tag
                v-for="step in s.steps"
                :key="step"
                size="small"
                type="info"
                class="task-tag"
              >
                {{ step }}
              </el-tag>
            </div>

            <div class="scenario-action">
              <el-button
                v-if="runningScenario === s.name"
                type="danger"
                size="small"
                @click="handleStop"
                :loading="operating"
              >
                停止
              </el-button>
              <el-button
                v-else
                type="primary"
                size="small"
                @click="handleStart(s.name)"
                :loading="operating"
                :disabled="runningScenario !== null"
              >
                启动
              </el-button>
            </div>
          </el-card>
        </div>

        <el-empty v-if="!loading && scenarios.length === 0" description="暂无可用剧本" />
      </el-skeleton>
    </el-card>

    <!-- 运行中提示 -->
    <el-card v-if="runningScenario" shadow="hover" class="running-card">
      <div class="running-info">
        <el-tag type="warning" effect="dark">运行中</el-tag>
        <span>剧本 "{{ runningScenario }}" 正在运行</span>
        <el-button type="danger" size="small" @click="handleStop" :loading="operating">停止剧本</el-button>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.scenarios-view {
  max-width: 900px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.scenario-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px;
}
.scenario-card {
  text-align: center;
}
.scenario-card.is-running {
  border: 2px solid #e6a23c;
}
.scenario-icon {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  color: #fff;
  font-size: 20px;
  font-weight: 600;
  line-height: 48px;
  margin: 0 auto 12px;
}
.scenario-name {
  margin: 0 0 8px;
  font-size: 16px;
  color: #303133;
}
.scenario-desc {
  margin: 0 0 12px;
  font-size: 13px;
  color: #909399;
  line-height: 1.4;
}
.scenario-tasks {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  justify-content: center;
  margin-bottom: 12px;
}
.task-tag {
  font-size: 11px;
}
.scenario-action {
  margin-top: 8px;
}
.running-card {
  margin-top: 16px;
}
.running-info {
  display: flex;
  align-items: center;
  gap: 12px;
}
</style>