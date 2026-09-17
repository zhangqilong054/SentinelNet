import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    // Element Plus 组件按需自动导入（<el-xxx> 模板标签）
    // ElMessage/ElMessageBox 等命令式 API 仍需手动导入
    Components({
      resolvers: [ElementPlusResolver()],
      dts: 'src/components.d.ts',
    }),
  ],

  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },

  // 开发模式：代理 API（含 SSE /api/stream）请求到后端
  // T3 审计清理：移除旧前端遗留的 /stream /login /logout /change-password 代理，
  // 新 SPA 全部走 /api 前缀（认证为 /api/auth/*）
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:5000',
        changeOrigin: true,
      },
    },
  },

  // 生产构建：输出到 dist/
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: true,
    rollupOptions: {
      output: {
        // 手动分块：将大型依赖拆分为独立 chunk（函数形式，兼容 Rolldown）
        manualChunks(id) {
          if (id.includes('node_modules/echarts/') || id.includes('node_modules/zrender/')) {
            return 'echarts'
          }
          if (id.includes('node_modules/element-plus/') || id.includes('node_modules/@element-plus/')) {
            return 'element-plus'
          }
          if (id.includes('node_modules/vue/') || id.includes('node_modules/vue-router/') || id.includes('node_modules/pinia/')) {
            return 'vue-vendor'
          }
        },
      },
    },
  },
})