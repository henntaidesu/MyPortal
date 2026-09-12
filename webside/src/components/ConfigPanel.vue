<script setup>
/**
 * 「系统配置」页：settings 表里那几项运行期配置。只有管理员能打开，后端也会再校验一次。
 *
 * 没有保存按钮，改完就存：文本框和数字框在 change（失焦或回车）时提交，下拉框选中即提交。
 * 用 change 而不是每敲一下就发，是因为中途的半截值（比如把 720 改成 30 的那一下是 "7"）
 * 也是合法值，会被真存进去。
 */
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { api } from '../api'

const items = ref([])
const draft = ref({})          // name -> 正在编辑的值
const flash = ref({})          // name -> 'ok' | 'err'，存完闪一下边框
const error = ref('')
const loading = ref(true)

async function reload() {
  try {
    const data = await api('/api/settings')
    items.value = data.items
    draft.value = Object.fromEntries(data.items.map((i) => [i.name, i.value]))
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function mark(name, kind) {
  flash.value[name] = kind
  setTimeout(() => {
    if (flash.value[name] === kind) delete flash.value[name]
  }, 1400)
}

async function save(item) {
  const value = String(draft.value[item.name])
  if (value === item.value) return
  error.value = ''
  try {
    const res = await api(`/api/settings/${item.name}`, { method: 'PUT', body: { value } })
    // 用后端规范化后的值回填：填 "ON" 存进去是 "true"
    item.value = res.value
    item.changed = res.value !== item.default
    draft.value[item.name] = res.value
    mark(item.name, 'ok')
  } catch (e) {
    error.value = e.message
    mark(item.name, 'err')     // 存失败就把值留在框里，别把人正在改的东西抢回去
  }
}

function reset(item) {
  draft.value[item.name] = item.default
  save(item)
}

/* Esc 关掉时输入框来不及失焦，走之前再提交一遍没落下的 */
onBeforeUnmount(() => items.value.forEach(save))
onMounted(reload)
</script>

<template>
  <p v-if="loading" class="muted">正在读取…</p>
  <p v-else-if="error && !items.length" class="err">{{ error }}</p>

  <div v-for="item in items" :key="item.name" class="row" :class="flash[item.name]">
    <label :for="'s-' + item.name">{{ item.name }}</label>

    <div class="ctl">
      <select
        v-if="item.kind === 'bool'"
        :id="'s-' + item.name"
        v-model="draft[item.name]"
        @change="save(item)"
      >
        <option value="true">true</option>
        <option value="false">false</option>
      </select>
      <input
        v-else
        :id="'s-' + item.name"
        v-model="draft[item.name]"
        :type="item.kind === 'int' ? 'number' : 'text'"
        :min="item.kind === 'int' ? item.low : null"
        :max="item.kind === 'int' ? item.high : null"
        @change="save(item)"
      />
      <button class="btn" :disabled="item.value === item.default" @click="reset(item)">复原</button>
    </div>
  </div>

  <p v-if="error && items.length" class="err">{{ error }}</p>
</template>

<style scoped>
.muted { color: var(--text-3); font-size: 13px; }

.row {
  padding: 13px 0;
  border-top: 1px solid var(--border);
}
.row:first-child { border-top: none; }
label { font-size: 13.5px; font-weight: 600; }

.ctl {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}
.ctl input, .ctl select { flex: 1 1 180px; min-width: 0; }

/* 存完闪一下边框，替掉原来的「已保存」字样 */
.row.ok input, .row.ok select { border-color: var(--ok, #2e9e5b); }
.row.err input, .row.err select { border-color: var(--danger); }

.err { color: var(--danger); font-size: 13px; margin: 14px 0 0; }
</style>
