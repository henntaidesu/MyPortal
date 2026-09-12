<script setup>
/**
 * 设置面板的外壳，里面分三页：
 *   我的账号   人人都有，改自己的用户名 / 显示名 / 邮箱 / 密码
 *   用户管理   建号、改号、重置密码、停用、删除
 *   系统配置   settings 表里那几项运行期配置
 *
 * 后两页只给 roles 里带 admin 的人看。前端这层藏按钮只是别摆个点不动的东西，
 * 真正拦人的是后端 deps.require_admin，每个管理接口都会再校验一次。
 */
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import AccountPanel from './AccountPanel.vue'
import UsersPanel from './UsersPanel.vue'
import ConfigPanel from './ConfigPanel.vue'
import { auth } from '../auth'

const emit = defineEmits(['close'])

const isAdmin = computed(() => !!auth.user?.roles?.includes('admin'))
const tabs = computed(() => [
  { key: 'account', label: '我的账号' },
  ...(isAdmin.value
    ? [{ key: 'users', label: '用户管理' }, { key: 'config', label: '系统配置' }]
    : [])
])
const tab = ref('account')

/* 三页内容长短不一，但对话框大小是钉死的（见下面的 .dialog），只有中间这块滚。
   换页时把它滚回顶上，不然从翻到一半的「系统配置」切过去会停在半空 */
const body = ref(null)
watch(tab, () => body.value?.scrollTo(0, 0))

function onKey(e) {
  if (e.key === 'Escape') emit('close')
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div class="mask" @mousedown.self="emit('close')">
    <div class="dialog">
      <div class="head">
        <h3>设置</h3>

        <div v-if="tabs.length > 1" class="tabs">
          <button
            v-for="t in tabs"
            :key="t.key"
            class="tab"
            :class="{ on: tab === t.key }"
            @click="tab = t.key"
          >{{ t.label }}</button>
        </div>
      </div>

      <div ref="body" class="body">
        <AccountPanel v-if="tab === 'account'" />
        <UsersPanel v-else-if="tab === 'users'" />
        <ConfigPanel v-else-if="tab === 'config'" />
      </div>

      <div class="foot">
        <button class="btn" @click="emit('close')">关闭</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 18, 24, .45);
  backdrop-filter: blur(3px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  z-index: 50;
  animation: fade .15s;
}
/* 大小是钉死的：三页内容长短差很多，跟着内容走的话切一下页对话框就蹦一下。
   头和脚不动，只有中间的 .body 滚；滚动条用 scrollbar-gutter 占着位，
   免得某一页不用滚时内容跟着横向挪 8 个像素 */
.dialog {
  display: flex;
  flex-direction: column;
  width: 620px;
  max-width: 100%;
  height: min(820px, calc(100vh - 40px));   /* 40px = 遮罩上下各 20px 的内边距 */
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-lg);
  animation: pop .16s cubic-bezier(.2, .9, .3, 1.2);
}
.head { flex: none; padding: 22px 22px 0; }
.body {
  flex: 1;
  min-height: 0;              /* 不写这行 flex 子项不肯缩，滚动条会长到外面去 */
  overflow-y: auto;
  scrollbar-gutter: stable;
  padding: 0 22px 6px;
}
h3 { margin: 0 0 14px; font-size: 16px; }

.tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 16px;
  padding: 3px;
  background: var(--surface-2);
  border-radius: var(--radius-sm);
}
.tab {
  flex: 1;
  height: 32px;
  border-radius: 7px;
  color: var(--text-2);
  font-size: 13px;
  transition: background .15s, color .15s;
}
.tab:hover { color: var(--text); }
.tab.on {
  background: var(--surface);
  color: var(--text);
  font-weight: 600;
  box-shadow: var(--shadow);
}

.foot {
  flex: none;
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 16px 22px 22px;
  border-top: 1px solid var(--border);
}
@keyframes fade { from { opacity: 0 } }
@keyframes pop { from { opacity: 0; transform: translateY(8px) scale(.98) } }
</style>
