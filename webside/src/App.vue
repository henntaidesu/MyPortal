<script setup>
import { ref, computed, onMounted } from 'vue'
import NavCard from './components/NavCard.vue'
import CardDialog from './components/CardDialog.vue'
import LoginView from './components/LoginView.vue'
import { state, removeItem, moveItem, exportJson, importJson, applyTheme, bindUser, unbindUser } from './store'
import { auth, refresh, logout, safeNext } from './auth'
import { download } from './utils'

const keyword = ref('')
const dialog = ref(null)   // { item } | null，null 表示不显示
const fileInput = ref(null)

/* 从 /sso/authorize 弹回来时带的落脚点，登录成功后原样跳回去 */
const next = safeNext(new URLSearchParams(location.search).get('next'))

const list = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return state.items
  return state.items.filter((i) =>
    [i.name, i.desc, i.url].some((f) => (f || '').toLowerCase().includes(kw))
  )
})

function askRemove(item) {
  if (confirm('删除「' + item.name + '」？')) removeItem(item.id)
}

/* 拖拽排序：搜索状态下不排序，避免索引错位 */
const dragIndex = ref(-1)
const overIndex = ref(-1)
const sortable = computed(() => !keyword.value.trim())

function onDrop(i) {
  if (dragIndex.value > -1) moveItem(dragIndex.value, i)
  dragIndex.value = -1
  overIndex.value = -1
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

/* 登录之后：装上这个人的导航数据，该跳业务系统的就直接跳过去 */
function enter() {
  bindUser(auth.user.username)
  if (next) {
    location.replace(next)
    return
  }
  if (location.search) history.replaceState(null, '', location.pathname)
}

async function doLogout() {
  if (!confirm('退出登录？已经打开的系统也会一并退出。')) return
  await logout()
  unbindUser()
}

onMounted(async () => {
  applyTheme()
  await refresh()
  if (auth.user) enter()
})
</script>

<template>
  <p v-if="!auth.ready" class="booting">正在检查登录状态…</p>

  <LoginView v-else-if="!auth.user" :next="next" @done="enter" />

  <div v-else class="page">
    <header>
      <h1 contenteditable spellcheck="false" @blur="state.title = $event.target.innerText.trim() || '我的导航'">
        {{ state.title }}
      </h1>
      <div class="top">
        <input v-model="keyword" class="search" placeholder="搜索…" />
        <button class="btn icon" :title="'主题：' + state.theme" @click="toggleTheme">{{ themeIcon }}</button>
        <button class="btn who" :title="'已登录：' + auth.user.username + '，点击退出'" @click="doLogout">
          <span class="dn">{{ auth.user.display_name }}</span>
          <span class="out">退出</span>
        </button>
      </div>
    </header>

    <div class="grid">
      <div
        v-for="(item, i) in list"
        :key="item.id"
        class="slot"
        :class="{ over: overIndex === i, dragging: dragIndex === i }"
        :draggable="sortable"
        @dragstart="dragIndex = i"
        @dragover.prevent="sortable && (overIndex = i)"
        @dragleave="overIndex === i && (overIndex = -1)"
        @drop.prevent="sortable && onDrop(i)"
        @dragend="dragIndex = -1; overIndex = -1"
      >
        <NavCard :item="item" @edit="(x) => (dialog = { item: x })" @remove="askRemove" />
      </div>

      <button v-if="!keyword.trim()" class="add" @click="dialog = { item: null }">
        <span class="plus">＋</span>
        <span>添加导航</span>
      </button>
    </div>

    <p v-if="keyword.trim() && !list.length" class="empty">没有匹配「{{ keyword }}」的导航</p>

    <footer>
      <span>共 {{ state.items.length }} 个</span>
      <span class="dot">·</span>
      <button class="link" @click="doExport">导出备份</button>
      <span class="dot">·</span>
      <button class="link" @click="fileInput.click()">导入备份</button>
      <input ref="fileInput" type="file" accept=".json" hidden @change="onFile" />
    </footer>

    <CardDialog v-if="dialog" :item="dialog.item" @close="dialog = null" />
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

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
  gap: 16px;
}
.slot { border-radius: var(--radius); transition: opacity .15s; }
.slot.dragging { opacity: .35; }
.slot.over { box-shadow: 0 0 0 2px var(--primary); }

.add {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  min-height: 90px;
  border: 1px dashed var(--border);
  border-radius: var(--radius);
  color: var(--text-3);
  font-size: 14px;
  transition: border-color .15s, color .15s, background .15s;
}
.add:hover {
  border-color: var(--primary);
  color: var(--primary);
  background: var(--surface);
}
.plus { font-size: 26px; line-height: 1; }

.empty { color: var(--text-3); text-align: center; padding: 30px; font-size: 13px; }

footer {
  margin-top: 36px;
  color: var(--text-3);
  font-size: 12px;
}
.dot { margin: 0 6px; }
.link { color: var(--text-3); text-decoration: underline; text-underline-offset: 3px; }
.link:hover { color: var(--primary); }

@media (max-width: 600px) {
  .page { padding: 28px 16px; }
  .grid { grid-template-columns: 1fr; }
  .top { width: 100%; margin-left: 0; }
  .search { flex: 1; width: auto; }
}
</style>
