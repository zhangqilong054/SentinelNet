import { createApp } from 'vue'
import { createPinia } from 'pinia'
import router from './router'
import App from './App.vue'
// Element Plus 暗色模式 CSS 变量（html.dark 类切换生效）
import 'element-plus/theme-chalk/dark/css-vars.css'
// 命令式 API（ElMessage/ElMessageBox/v-loading）经显式 import 调用，
// 绕过了 unplugin 模板扫描的样式注入 → 必须手动引入对应样式，
// 否则 MessageBox 全屏宽裸渲染、Toast 无样式（2026-09-19 真机复现）
import 'element-plus/theme-chalk/el-message.css'
import 'element-plus/theme-chalk/el-message-box.css'
import 'element-plus/theme-chalk/el-loading.css'
import { initTheme } from './theme'

initTheme()

// Element Plus 按需导入：模板组件（<el-xxx>）由 unplugin-vue-components 自动处理；
// 命令式 API 需手动导入函数与上述样式
// 图标组件仍需手动导入（仅在使用处按需引入）

const app = createApp(App)

app.use(createPinia())
app.use(router)
app.mount('#app')