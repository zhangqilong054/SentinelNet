<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { getSettings, updateSettings, analyzePayload } from '@/api/endpoints'
import { ElMessage } from 'element-plus'

// ── 阈值配置 ──────────────────────────────────────────
const loading = ref(false)
const saving = ref(false)
const thresholds = ref<Record<string, number>>({})
const authEnabled = ref(false)
const mlConfig = ref<Record<string, unknown>>({})

// 阈值元信息（显示名称、范围、步长）
const thresholdMeta: Record<string, { label: string; min: number; max: number; step: number }> = {
  ddos_threshold: { label: 'DDoS 阈值', min: 100, max: 10000, step: 50 },
  port_scan_threshold: { label: '端口扫描阈值', min: 10, max: 1000, step: 10 },
  syn_flood_threshold: { label: 'SYN Flood 阈值', min: 50, max: 5000, step: 50 },
  udp_flood_threshold: { label: 'UDP Flood 阈值', min: 50, max: 5000, step: 50 },
  brute_force_threshold: { label: '暴力破解阈值', min: 5, max: 100, step: 1 },
  lateral_threshold: { label: '横向移动阈值', min: 5, max: 200, step: 5 },
}

function getMeta(key: string) {
  return thresholdMeta[key] || { label: key, min: 0, max: 10000, step: 1 }
}

async function fetchSettings() {
  loading.value = true
  try {
    const data = await getSettings()
    if (data) {
      thresholds.value = data.thresholds ?? {}
      authEnabled.value = data.auth_enabled ?? false
      mlConfig.value = (data.ml_config as Record<string, unknown>) ?? {}
    }
  } catch (e) {
    ElMessage.error('加载配置失败')
  } finally {
    loading.value = false
  }
}

async function handleThresholdChange(key: string, value: number) {
  saving.value = true
  try {
    await updateSettings({ key, value })
    thresholds.value[key] = value
    ElMessage.success(`${getMeta(key).label} 已更新为 ${value}`)
  } catch (e) {
    ElMessage.error('更新阈值失败')
    // 回退
    await fetchSettings()
  } finally {
    saving.value = false
  }
}

// ── 载荷分析 ──────────────────────────────────────────
const payloadForm = reactive({
  data: '',
  format: 'hex' as 'hex' | 'base64',
})
const analyzing = ref(false)
const analysisResult = ref<{
  risk_score: number
  summary: string
  findings: Record<string, unknown>[]
} | null>(null)

async function handleAnalyze() {
  if (!payloadForm.data.trim()) {
    ElMessage.warning('请输入载荷数据')
    return
  }
  analyzing.value = true
  analysisResult.value = null
  try {
    const result = await analyzePayload({
      data: payloadForm.data.trim(),
      format: payloadForm.format,
    })
    if (result) {
      analysisResult.value = {
        risk_score: result.risk_score ?? 0,
        summary: result.summary ?? '',
        findings: (result.findings as Record<string, unknown>[]) ?? [],
      }
    }
  } catch (e) {
    ElMessage.error('载荷分析失败')
  } finally {
    analyzing.value = false
  }
}

function riskLevel(score: number): 'success' | 'warning' | 'danger' | 'info' {
  if (score >= 80) return 'danger'
  if (score >= 50) return 'warning'
  if (score >= 20) return 'info'
  return 'success'
}

onMounted(fetchSettings)
</script>

<template>
  <div class="config-view">
    <!-- 阈值配置 -->
    <el-card shadow="hover" class="config-card">
      <template #header>
        <div class="card-header">
          <span>阈值配置</span>
          <el-button size="small" @click="fetchSettings" :loading="loading">刷新</el-button>
        </div>
      </template>

      <el-skeleton :loading="loading" :rows="6" animated>
        <div class="threshold-list">
          <div v-for="(value, key) in thresholds" :key="key" class="threshold-item">
            <div class="threshold-label">
              <span>{{ getMeta(key).label }}</span>
              <el-tag size="small" type="info">{{ value }}</el-tag>
            </div>
            <el-slider
              :model-value="value"
              :min="getMeta(key).min"
              :max="getMeta(key).max"
              :step="getMeta(key).step"
              :disabled="saving"
              show-input
              @change="(v: number | number[]) => handleThresholdChange(key, v as number)"
            />
          </div>

          <el-empty v-if="Object.keys(thresholds).length === 0" description="暂无阈值配置" />
        </div>
      </el-skeleton>
    </el-card>

    <!-- 载荷分析 -->
    <el-card shadow="hover" class="config-card">
      <template #header>
        <span>载荷送检</span>
      </template>

      <el-form label-width="80px">
        <el-form-item label="数据格式">
          <el-radio-group v-model="payloadForm.format">
            <el-radio value="hex">Hex</el-radio>
            <el-radio value="base64">Base64</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="载荷数据">
          <el-input
            v-model="payloadForm.data"
            type="textarea"
            :rows="4"
            :placeholder="payloadForm.format === 'hex'
              ? '输入十六进制载荷，如: 48454c4c4f'
              : '输入Base64编码载荷'"
            style="font-family: 'Courier New', monospace"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="handleAnalyze" :loading="analyzing">
            分析载荷
          </el-button>
        </el-form-item>
      </el-form>

      <!-- 分析结果 -->
      <div v-if="analysisResult" class="analysis-result">
        <el-divider>分析结果</el-divider>

        <el-descriptions :column="2" border>
          <el-descriptions-item label="风险评分">
            <el-tag :type="riskLevel(analysisResult.risk_score)" size="large">
              {{ analysisResult.risk_score }}/100
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="摘要" :span="2">
            {{ analysisResult.summary }}
          </el-descriptions-item>
        </el-descriptions>

        <div v-if="analysisResult.findings.length > 0" class="findings">
          <h4>发现</h4>
          <el-table :data="analysisResult.findings" stripe size="small" max-height="200">
            <el-table-column
              v-for="(_, key) in analysisResult.findings[0]"
              :key="key"
              :prop="String(key)"
              :label="String(key)"
              show-overflow-tooltip
            />
          </el-table>
        </div>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.config-view {
  max-width: 900px;
}
.config-card {
  margin-bottom: 16px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.threshold-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.threshold-item {
  padding: 0 8px;
}
.threshold-label {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
  font-size: 14px;
  color: #303133;
}
.analysis-result {
  margin-top: 8px;
}
.findings {
  margin-top: 12px;
}
.findings h4 {
  margin: 0 0 8px;
  font-size: 14px;
  color: #303133;
}
</style>