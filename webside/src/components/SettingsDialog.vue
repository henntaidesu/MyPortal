<script setup>
/** 系统设置。只有 roles 里带 admin 的人能打开，后端也会再校验一次。 */
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { api } from '../api'

const emit = defineEmits(['close'])

const items = ref([])
const draft = ref({})          // name -> 正在编辑的值
const saving = ref('')         // 正在保存哪一项
const error = ref('')
const okFlash = ref('')        // 刚存好的那项，闪一下「已保存」
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

function dirty(item) {
  return String(draft.value[item.name]) !== item.value
}

async function save(item) {
  error.value = ''
  saving.value = item.name
  try {
    const res = await api(`/api/settings/${item.name}`, {
      method: 'PUT',
      body: { value: String(draft.value[item.name]) }
    })
    // 用后端规范化后的值回填：填 "ON" 存进去是 "true"
    item.value = res.value
    item.changed = res.value !== item.default
    draft.value[item.name] = res.value
    okFlash.value = item.name
    setTimeout(() => (okFlash.value === item.name) && (okFlash.value = ''), 1600)
  } catch (e) {
    error.value = e.message
    draft.value[item.name] = item.value      // 存失败就把输入框退回原值，免得看着像已经生效
  } finally {
    saving.value = ''
  }
}

function reset(item) {
  draft.value[item.name] = item.default
  save(item)
}

function onKey(e) {
  if (e.key === 'Escape') emit('close')
}
onMounted(() => {
  window.addEventListener('keydown', onKey)
  reload()
})
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div class="mask" @mousedown.self="emit('close')">
    <div class="dialog">
      <h3>系统设置</h3>
      <p class="lead">
        这些配置存在数据库里，改完最多 5 秒生效，不用重启。
        数据库连接和监听端口不在这里，它们在 <code>backend/conf.ini</code>，改完要重启。
      </p>

      <p v-if="loading" class="muted">正在读取…</p>
      <p v-else-if="error && !items.length" class="err">{{ error }}</p>

      <div v-for="item in items" :key="item.name" class="row">
        <div class="head">
          <label :for="'s-' + item.name">{{ item.name }}</label>
          <span v-if="item.changed" class="tag">已改过默认值</span>
        </div>
        <p class="note">{{ item.note }}</p>

        <div class="ctl">
          <select v-if="item.kind === 'bool'" :id="'s-' + item.name" v-model="draft[item.name]">
            <option value="true">true（开）</option>
            <option value="false">false（关）</option>
          </select>
          <input
            v-else-if="item.kind === 'int'"
            :id="'s-' + item.name"
            v-model="draft[item.name]"
            type="number"
            :min="item.low"
            :max="item.high"
            @keyup.enter="save(item)"
          />
          <input
            v-else
            :id="'s-' + item.name"
            v-model="draft[item.name]"
            @keyup.enter="save(item)"
          />

          <button class="btn primary" :disabled="!dirty(item) || saving === item.name" @click="save(item)">
            {{ saving === item.name ? '保存中' : '保存' }}
          </button>
          <button class="btn" :disabled="item.value === item.default" @click="reset(item)">
            复原
          </button>
          <span v-if="okFlash === item.name" class="ok">已保存</span>
        </div>

        <p class="meta">
          默认 <code>{{ item.default }}</code>
          <template v-if="item.kind === 'int'">，可填 {{ item.low }} ~ {{ item.high }}</template>
        </p>
        <p v-if="item.caution" class="caution">注意：{{ item.caution }}</p>
      </div>

      <p v-if="error && items.length" class="err">{{ error }}</p>

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
.dialog {
  width: 560px;
  max-width: 100%;
  max-height: 90vh;
  overflow: auto;
  padding: 22px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-lg);
  animation: pop .16s cubic-bezier(.2, .9, .3, 1.2);
}
h3 { margin: 0 0 10px; font-size: 16px; }
.lead {
  margin: 0 0 18px;
  color: var(--text-3);
  font-size: 12.5px;
  line-height: 1.75;
}
.muted { color: var(--text-3); font-size: 13px; }

.row {
  padding: 14px 0;
  border-top: 1px solid var(--border);
}
.head { display: flex; align-items: center; gap: 8px; }
label { font-size: 13.5px; font-weight: 600; }
.tag {
  font-size: 11px;
  color: var(--text-3);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 1px 8px;
}
.note { margin: 4px 0 0; color: var(--text-2); font-size: 12.5px; }

.ctl {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 9px;
}
.ctl input, .ctl select { flex: 1 1 180px; min-width: 0; }
.ok { color: var(--ok, #2e9e5b); font-size: 12.5px; }

.meta { margin: 7px 0 0; color: var(--text-3); font-size: 11.5px; }
.meta code {
  background: var(--surface-2);
  border-radius: 4px;
  padding: 1px 5px;
}
.caution {
  margin: 6px 0 0;
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--danger);
}
.err { color: var(--danger); font-size: 13px; margin: 14px 0 0; }
.foot { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; }
@keyframes fade { from { opacity: 0 } }
@keyframes pop { from { opacity: 0; transform: translateY(8px) scale(.98) } }
</style>
