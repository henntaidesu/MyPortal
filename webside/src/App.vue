<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import NavGroup from './components/NavGroup.vue'
import CardDialog from './components/CardDialog.vue'
import LoginView from './components/LoginView.vue'
import {
  state, sync, totalItems, removeItem, addGroup, removeGroup,
  exportJson, importJson, applyTheme, boot, flushNav, unbind
} from './store'
import { end as endDrag } from './drag'
import { auth, refresh, logout } from './auth'
import { download } from './utils'

const keyword = ref('')
const dialog = ref(null)   // { group, item } | null，item 为 null 表示新增
const ready = ref(false)   // 和后端对完账了，之前先别显示「共 0 个」
const fileInput = ref(null)

/* 搜索：按分类分别过滤，空分类直接不显示。搜索状态下不给拖——
   显示的下标和 group.items 里的真实下标对不上，拖了会错位 */
const sortable = computed(() => !keyword.value.trim())
const view = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return state.groups.map((g) => ({ group: g, items: g.items }))
  return state.groups
    .map((g) => ({
      group: g,
      items: g.items.filter((i) =>
        [i.name, i.desc, i.url].some((f) => (f || '').toLowerCase().includes(kw))
      )
    }))
    .filter((row) => row.items.length)
})

function askRemoveItem({ group, item }) {
  if (confirm('删除「' + item.name + '」？')) removeItem(group.id, item.id)
}

function askRemoveGroup(group) {
  const n = group.items.length
  const tip = n
    ? `删除分类「${group.name}」？里面 ${n} 个导航会一起删掉。`
    : `删除分类「${group.name}」？`
  if (confirm(tip)) removeGroup(group.id)
}

function newGroup() {
  const name = prompt('新分类叫什么？', '新分类')
  if (name !== null) addGroup(name)
}

function doExport() {
  download('导航备份-' + new Date().toISOString().slice(0, 10) + '.json', exportJson())
}

function onFile(e) {
  const file = e.target.files && e.target.files[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => {
    try {
      importJson(String(reader.result))
    } catch (err) {
      alert('导入失败：不是合法的备份文件')
    }
  }
  reader.readAsText(file)
  e.target.value = ''
}

function toggleTheme() {
  const order = ['auto', 'light', 'dark']
  state.theme = order[(order.indexOf(state.theme) + 1) % 3]
}
const themeIcon = computed(() => ({ auto: '🌗', light: '☀️', dark: '🌙' }[state.theme]))

/* 登录之后：装上导航数据，开始自动保存 */
async function enter() {
  ready.value = false
  await boot()
  ready.value = true
}

async function doLogout() {
  if (!confirm('退出登录？')) return
  await flushNav()          // 攒着的改动先落盘，不然跟着会话一起没了
  await logout()
  unbind()
}

/**
 * 进出登录统一由 auth.user 驱动，一处管三种情况：开页面时已经登着、刚登录、会话过期。
 *
 * **别改回「LoginView 登录成功后 emit 一下，App 收到再 enter()」**：登录成功那一刻
 * auth.user 被赋值，Vue 随即把 LoginView 卸载掉，emit 正好赶在自己被卸载的同一拍上，
 * 事件就丢了——表现是登录后页面进来了，但底部一直「正在同步…」，
 * 后端一个 /api/nav 请求都没发出去。
 */
watch(() => auth.user, (user) => {
  if (user) enter()
  else unbind()          // 会话过期：停掉自动保存，不然它会对着 401 一直重试
})

onMounted(async () => {
  applyTheme()
  await refresh()        // 问出身份之后，上面那个 watch 会接手
})

/* 拖到页面空白处松手：浏览器不会给任何一个分类发 drop，状态得自己收干净，
   不然高亮会一直挂在那儿 */
function onPageDrop() {
  endDrag()
}
</script>

<template>
  <p v-if="!auth.ready" class="booting">正在检查登录状态…</p>

  <LoginView v-else-if="!auth.user" />

  <div v-else class="page" @dragover.prevent @drop.prevent="onPageDrop" @dragend="onPageDrop">
    <header>
      <h1 contenteditable spellcheck="false" @blur="state.title = $event.target.innerText.trim() || '我的门户'">
        {{ state.title }}
      </h1>
      <div class="top">
        <input v-model="keyword" class="search" placeholder="搜索…" />
        <button class="btn icon" :title="'主题：' + state.theme" @click="toggleTheme">{{ themeIcon }}</button>
        <button class="btn who" :title="'已登录：' + auth.user.username + '，点击退出'" @click="doLogout">
          <span class="dn">{{ auth.user.username }}</span>
          <span class="out">退出</span>
        </button>
      </div>
    </header>

    <p v-if="auth.defaultPassword" class="warn">
      还在用默认口令 admin / admin —— 编辑后端的 <code>conf.json</code> →
      <code>auth.password</code> 改掉它，然后重启。
    </p>

    <div class="groups">
      <NavGroup
        v-for="row in view"
        :key="row.group.id"
        :group="row.group"
        :items="row.items"
        :sortable="sortable"
        @add="(g) => (dialog = { group: g, item: null })"
        @edit="(x) => (dialog = { group: x.group, item: x.item })"
        @remove-item="askRemoveItem"
        @remove-group="askRemoveGroup"
      />

      <button v-if="sortable" class="addgroup" @click="newGroup">
        <span class="plus">＋</span>
        <span>新建分类</span>
      </button>
    </div>

    <p v-if="keyword.trim() && !view.length" class="empty">没有匹配「{{ keyword }}」的导航</p>

    <footer>
      <span v-if="!ready">正在同步…</span>
      <span v-else>{{ state.groups.length }} 个分类 · 共 {{ totalItems() }} 个导航</span>
      <span v-if="sync.offline" class="offline" title="改动先存在这台机器上，下次连上后端会自动补传">
        · 连不上后端，改动暂时只存在本机
      </span>
      <span class="dot">·</span>
      <button class="link" @click="doExport">导出备份</button>
      <span class="dot">·</span>
      <button class="link" @click="fileInput.click()">导入备份</button>
      <input ref="fileInput" type="file" accept=".json" hidden @change="onFile" />
    </footer>

    <CardDialog
      v-if="dialog"
      :group="dialog.group"
      :item="dialog.item"
      @close="dialog = null"
    />
  </div>
</template>

<style scoped>
.booting {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  margin: 0;
  color: var(--text-3);
  font-size: 13px;
}

.page {
  padding: 40px 36px 48px;
}

header {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 26px;
}
h1 {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  outline: none;
  border-radius: 6px;
  padding: 2px 4px;
}
h1:hover { background: var(--surface-hover); }
h1:focus { background: var(--surface); box-shadow: 0 0 0 2px var(--primary); }

.top { display: flex; gap: 8px; margin-left: auto; }
.search { width: 200px; height: 36px; background: var(--surface); }

.who { max-width: 170px; }
.dn { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.out { color: var(--text-3); font-size: 12px; }
.who:hover .out { color: var(--danger); }

.warn {
  margin: -10px 0 22px;
  padding: 9px 13px;
  border: 1px solid var(--border);
  border-left: 3px solid var(--danger);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--text-2);
  font-size: 12.5px;
}
.warn code {
  background: var(--surface-2);
  border-radius: 4px;
  padding: 1px 5px;
}

.groups { display: flex; flex-direction: column; gap: 16px; }

.addgroup {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 52px;
  border: 1px dashed var(--border);
  border-radius: var(--radius);
  color: var(--text-3);
  font-size: 13.5px;
  transition: border-color .15s, color .15s, background .15s;
}
.addgroup:hover {
  border-color: var(--primary);
  color: var(--primary);
  background: var(--surface);
}
.plus { font-size: 20px; line-height: 1; }

.empty { color: var(--text-3); text-align: center; padding: 30px; font-size: 13px; }

footer {
  margin-top: 36px;
  color: var(--text-3);
  font-size: 12px;
}
.dot { margin: 0 6px; }
.offline { color: var(--danger); margin-left: 4px; }
.link { color: var(--text-3); text-decoration: underline; text-underline-offset: 3px; }
.link:hover { color: var(--primary); }

@media (max-width: 600px) {
  .page { padding: 28px 16px; }
  .top { width: 100%; margin-left: 0; }
  .search { flex: 1; width: auto; }
}
</style>
