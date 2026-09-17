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
} from '@element-plus/icons-vue'
import { ElMessageBox, ElMessage } from 'element-plus'


const route = useRoute()
const router = useRouter()
const sseStore = useSseStore()
const authStore = useAuthStore()

// SSE 连接控制 — 登录后自动连接
const sseEnabled = ref(true)
useSSE(sseEnabled)

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

      <!-- 底部：SSE 状态 + 用户信息 -->
      <div class="aside-footer">
        <div class="sse-status">
          <el-tag
            :type="sseStore.status === 'connected' ? 'success' : sseStore.status === 'reconnecting' ? 'warning' : 'danger'"
            size="small"
            effect="dark"
          >
            {{ sseStore.statusText }}
          </el-tag>
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
      <router-view />
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
.sse-status {
  text-align: center;
  margin-bottom: 8px;
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
  background-color: #f0f2f5;
  overflow-y: auto;
  padding: 20px;
}
</style>