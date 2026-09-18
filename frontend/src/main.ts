import { createApp } from 'vue'
import { createPinia } from 'pinia'
import router from './router'
import App from './App.vue'

// Element Plus 按需导入：由 unplugin-vue-components 和 unplugin-auto-import 自动处理
// 组件（<el-xxx>）和 API（ElMessage, ElMessageBox）无需手动导入
// CSS 同样由 ElementPlusResolver 按需导入，无需全量引入
// 图标组件仍需手动导入（仅在使用处按需引入）

const app = createApp(App)

app.use(createPinia())
app.use(router)
app.mount('#app')