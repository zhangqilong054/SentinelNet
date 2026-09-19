<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSseStore } from '@/stores/sse'
import { useAuthStore } from '@/stores/auth'
import { useSSE } from '@/composables/useSSE'
import {
  Monitor,
  List,
  Setting,
  InfoFilled,
  SwitchButton,
  Film,
  Moon,
  Sunny,
  RefreshRight,
} from '@element-plus/icons-vue'
import { ElMessageBox, ElMessage } from 'element-plus'
import { isDark, toggleTheme } from '@/theme'


const route = useRoute()
const router = useRouter()
const sseStore = useSseStore()
const authStore = useAuthStore()

// SSE 连接控制 — 登录后自动连接；保留 open 供底部状态标签手动重连
const sseEnabled = ref(true)
const sse = useSSE(sseEnabled)

/** 底部 SSE 状态标签可点击：未连接时手动重连（免等自动退避） */
function handleSseClick() {
  if (sseStore.status !== 'connected') {
    sse.open()
    ElMessage.info('正在尝试重新连接…')
  }
}

// ── 暗色模式 ──────────────────────────────────────────
const darkMode = ref(isDark())
function handleToggleTheme() {
  darkMode.value = toggleTheme()
}

const activeMenu = computed(() => {
  const path = route.path
  if (path.startsWith('/observe')) return '/observe'
  if (path.startsWith('/tasks')) return '/tasks'
  if (path.startsWith('/config')) return '/config'
  if (path.startsWith('/system')) return '/system'
  if (path.startsWith('/scenarios')) return '/scenarios'
  return '/observe'
})

function handleMenuSelect(index: string) {
  router.push(index)
}

async function handleLogout() {
  try {
    await ElMessageBox.confirm('确定要退出登录吗？', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    })
    sseEnabled.value = false
    await authStore.logout()
    ElMessage.success('已退出登录')
    router.push('/login')
  } catch {
    // 取消
  }
}

const navItems = [
  { index: '/observe', title: '观测', icon: Monitor },
  { index: '/tasks', title: '任务中心', icon: List },
  { index: '/config', title: '配置', icon: Setting },
  { index: '/system', title: '系统', icon: InfoFilled },
  { index: '/scenarios', title: '剧本', icon: Film },
]

onMounted(() => {
  authStore.checkSession()
})
</script>

<template>
  <el-container class="layout-container">
    <!-- 侧边导航 -->
    <el-aside width="200px" class="layout-aside">
      <div class="logo">
        <h2>SentinelNet</h2>
      </div>
      <el-menu
        :default-active="activeMenu"
        class="layout-menu"
        background-color="#304156"
        text-color="#bfcbd9"
        active-text-color="#409eff"
        @select="handleMenuSelect"
      >
        <el-menu-item
          v-for="item in navItems"
          :key="item.index"
          :index="item.index"
        >
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.title }}</span>
        </el-menu-item>
      </el-menu>

      <!-- 底部：主题开关 + SSE 状态 + 用户信息 -->
      <div class="aside-footer">
        <div class="footer-row">
          <span class="footer-label">外观</span>
          <el-button
            :icon="darkMode ? Sunny : Moon"
            size="small"
            text
            @click="handleToggleTheme"
          >{{ darkMode ? '亮色' : '暗色' }}</el-button>
        </div>
        <div class="sse-status">
          <el-tooltip content="未连接时点击可立即重连" :disabled="sseStore.status === 'connected'">
            <el-tag
              :type="sseStore.status === 'connected' ? 'success' : sseStore.status === 'reconnecting' ? 'warning' : 'danger'"
              size="small"
              effect="dark"
              :class="{ 'sse-clickable': sseStore.status !== 'connected' }"
              @click="handleSseClick"
            >
              {{ sseStore.statusText }}
              <el-icon v-if="sseStore.status !== 'connected'" class="sse-retry-icon"><RefreshRight /></el-icon>
            </el-tag>
          </el-tooltip>
        </div>
        <div class="user-info" v-if="authStore.isLoggedIn">
          <span class="username">{{ authStore.user }}</span>
          <el-button
            :icon="SwitchButton"
            size="small"
            text
            type="danger"
            @click="handleLogout"
          />
        </div>
      </div>
    </el-aside>

    <!-- 主内容区 -->
    <el-main class="layout-main">
      <router-view v-slot="{ Component }">
        <transition name="route-fade" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </el-main>
  </el-container>
</template>

<style scoped>
.layout-container {
  height: 100vh;
}
.layout-aside {
  background-color: #304156;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.logo {
  padding: 16px;
  text-align: center;
  color: #fff;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}
.logo h2 {
  margin: 0;
  font-size: 18px;
  letter-spacing: 1px;
}
.layout-menu {
  border-right: none;
  flex: 1;
}
.aside-footer {
  padding: 12px 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
}
.footer-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.footer-label {
  color: #bfcbd9;
  font-size: 12px;
}
.sse-status {
  text-align: center;
  margin-bottom: 8px;
}
.sse-clickable {
  cursor: pointer;
}
.sse-retry-icon {
  margin-left: 2px;
  vertical-align: -2px;
}
.user-info {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #bfcbd9;
  font-size: 12px;
}
.username {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.layout-main {
  /* 语义变量：暗色模式下自动切换为页面底色 */
  background-color: var(--el-bg-color-page);
  overflow-y: auto;
  padding: 20px;
}

/* 路由切换过渡 */
.route-fade-enter-active,
.route-fade-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}
.route-fade-enter-from {
  opacity: 0;
  transform: translateY(4px);
}
.route-fade-leave-to {
  opacity: 0;
}
</style>