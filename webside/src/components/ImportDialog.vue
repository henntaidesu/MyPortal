<script setup>
/**
 * 导入导航。三个来源，落到同一条路上：选文件、粘贴、从服务器上那份 conf.json 取。
 *
 * **解析和合并都在 store.js 里**（`parseImport` / `importNav`），这里只管把文本
 * 递过去、把结果显示出来。改完 state 之后存盘走的还是平常那条自动保存——
 * 导入不另开一条跟后端说话的通道，那是 store.js 刻意保持的边界。
 *
 * 先解析、显示「认出 N 个分类 M 张卡片」，再让人按按钮，不是选完文件就动手：
 * 「替换」会把现有的整棵树盖掉，那一下没有撤销。
 */
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { parseImport, fromObject, importNav } from '../store'
import { api } from '../api'
import { auth } from '../auth'

const emit = defineEmits(['close'])

const text = ref('')
const parsed = ref(null)      // { nav, groups, items, ... } | null
const error = ref('')
const done = ref(null)        // 导入完的结果
const fileInput = ref(null)
const server = ref(null)      // 服务器上那份 conf.json 里有没有东西

const canImport = computed(() => !!parsed.value && !done.value)

function parse(raw) {
  error.value = ''
  done.value = null
  try {
    parsed.value = parseImport(raw)
  } catch (e) {
    parsed.value = null
    error.value = e.message
  }
}

function onPaste() {
  if (!text.value.trim()) {
    parsed.value = null
    error.value = ''
    return
  }
  parse(text.value)
}

async function onFile(e) {
  const file = e.target.files?.[0]
  if (!file) return
  try {
    text.value = await file.text()
  } catch {
    error.value = '这个文件读不出来'
    return
  }
  parse(text.value)
  // 同一个文件再选一次也要能触发 change，所以把 input 清掉
  e.target.value = ''
}

/* 服务器上那份 conf.json 里的老导航。只有管理员问得到（后端挂的是 require_admin），
   所以普通用户这块整个不画 */
async function loadServer() {
  if (!auth.user?.is_admin) return
  try {
    const res = await api('/api/nav/legacy')
    server.value = res.available ? res : null
  } catch {
    server.value = null
  }
}

function useServer() {
  error.value = ''
  done.value = null
  try {
    parsed.value = fromObject(server.value.data)
    text.value = JSON.stringify(server.value.data, null, 2)
  } catch (e) {
    parsed.value = null
    error.value = e.message
  }
}

function run(mode) {
  if (!canImport.value) return
  if (mode === 'replace') {
    const tip = `用导入的这份**替换**现在的导航？\n\n`
      + `现有的分类和卡片会被全部清掉，换成导入的 ${parsed.value.groups} 个分类、`
      + `${parsed.value.items} 张卡片。这一步没有撤销。`
    if (!confirm(tip.replace(/\*\*/g, ''))) return
  }
  try {
    done.value = { ...importNav(parsed.value, mode), mode }
  } catch (e) {
    error.value = '导入失败：' + (e?.message || '未知错误')
  }
}

function onKey(e) {
  if (e.key === 'Escape') emit('close')
}
onMounted(() => {
  window.addEventListener('keydown', onKey)
  loadServer()
})
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div class="mask" @mousedown.self="emit('close')">
    <div class="dialog">
      <h3>导入导航</h3>

      <p class="hint">
        认整份 <code>conf.json</code>、认里面 <code>nav</code> 那一段，
        也认单独一个 <code>groups</code> 或 <code>items</code> 数组。
      </p>

      <div v-if="server" class="server">
        <div>
          服务器上的 <code>{{ server.path }}</code> 里还留着一份：
          {{ server.groups }} 个分类、{{ server.items }} 张卡片
        </div>
        <button class="btn" @click="useServer">读进来</button>
      </div>

      <div class="pick">
        <button class="btn" @click="fileInput?.click()">选一个 JSON 文件…</button>
        <input ref="fileInput" type="file" accept=".json,application/json" hidden @change="onFile" />
        <span class="or">或者把内容粘到下面</span>
      </div>

      <textarea v-model="text" rows="7" spellcheck="false"
                placeholder='{ "nav": { "groups": [ ... ] } }' @input="onPaste"></textarea>

      <p v-if="error" class="err">{{ error }}</p>

      <p v-if="parsed && !done" class="ok">
        认出 {{ parsed.groups }} 个分类、{{ parsed.items }} 张卡片。
      </p>

      <p v-if="done" class="ok">
        <template v-if="done.replaced">
          已替换：现在是 {{ done.groups }} 个分类、{{ done.items }} 张卡片。
        </template>
        <template v-else>
          已合并：新增 {{ done.groups }} 个分类、{{ done.items }} 张卡片<!--
          -->{{ done.skipped ? `，跳过 ${done.skipped} 张地址重复的` : '' }}。
        </template>
        改动会自动存到服务器。
      </p>

      <div class="foot">
        <button class="btn" @click="emit('close')">{{ done ? '完成' : '取消' }}</button>
        <button class="btn" :disabled="!canImport" @click="run('replace')">替换现有</button>
        <button class="btn primary" :disabled="!canImport" @click="run('merge')">合并进来</button>
      </div>

      <p class="hint foot-hint">
        <strong>合并</strong>：同名分类并到一起，同一个分类里地址重复的跳过。
        <strong>替换</strong>：现有的整棵树被盖掉，没有撤销。
      </p>
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
}
.dialog {
  width: 520px;
  max-width: 100%;
  max-height: 90vh;
  overflow: auto;
  padding: 22px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-lg);
}
h3 { margin: 0 0 10px; font-size: 16px; }
.hint { color: var(--text-3); font-size: 12px; line-height: 1.6; margin: 0 0 14px; }
.hint code { font-size: 11.5px; }
.hint strong { color: var(--text-2); }
.foot-hint { margin: 14px 0 0; }

.server {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 11px 12px;
  margin-bottom: 14px;
  border: 1px dashed var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-2);
  font-size: 12.5px;
  color: var(--text-2);
}
.server code { font-size: 11.5px; word-break: break-all; }

.pick { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.or { color: var(--text-3); font-size: 12px; }

textarea {
  width: 100%;
  resize: vertical;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
}

.err { color: var(--danger); font-size: 13px; margin: 12px 0 0; }
.ok { color: var(--primary); font-size: 13px; margin: 12px 0 0; }
.foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }
</style>
