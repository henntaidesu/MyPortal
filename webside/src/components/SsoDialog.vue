<script setup>
/**
 * 单点登录提供方的设置：总开关 + 门户地址（issuer），存在库里（app/idp.py 的 settings），
 * 改完即刻生效、不用重启。只有管理员看得到这个入口，真正的拦截在后端 require_admin。
 *
 * 下面那份列表只是「现在认哪些系统」的一览：客户端在导航卡片的编辑框里登记，
 * 这里不重复做一套增删改。做成一条一张的列表而不是表格：回调地址一长，
 * 表格的其余几列会被挤成一个字一行。
 */
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { api, ApiError } from '../api'

const emit = defineEmits(['close'])

const form = reactive({ enabled: false, issuer: '' })
const info = ref(null)        // 后端回的整份：enabled / issuer / request_origin / clients（都是已保存的）
const error = ref('')
const notice = ref('')
const busy = ref(false)

/* 没改就不让点保存：底部那个按钮只管上面两栏，下面的列表在这里改不了 */
const dirty = computed(() => !!info.value &&
  (form.enabled !== info.value.enabled || form.issuer.trim() !== info.value.issuer))

function fill(res) {
  info.value = res
  form.enabled = res.enabled
  form.issuer = res.issuer
}

async function load() {
  try {
    fill(await api('/api/idp/settings'))
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '读不出设置'
  }
}

async function save() {
  if (busy.value || !dirty.value) return
  busy.value = true
  error.value = notice.value = ''
  try {
    fill(await api('/api/idp/settings', {
      method: 'PUT', body: { enabled: form.enabled, issuer: form.issuer.trim() }
    }))
    notice.value = '已保存，即刻生效'
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '保存失败'
  } finally {
    busy.value = false
  }
}

function badge(c) {
  if (c.shadowed) return { text: '被 conf.json 覆盖', cls: 'bad' }
  return c.enabled ? { text: '生效', cls: 'on' } : { text: '已关闭', cls: '' }
}

function onKey(e) {
  if (e.key === 'Escape') emit('close')
}
onMounted(() => {
  window.addEventListener('keydown', onKey)
  load()
})
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div class="mask" @mousedown.self="emit('close')">
    <div class="dialog">
      <h3>单点登录</h3>

      <div class="grid">
        <label class="field">
          <span class="lb">单点登录提供方</span>
          <select v-model="form.enabled">
            <option :value="false">关闭</option>
            <option :value="true">开启</option>
          </select>
        </label>

        <label class="field">
          <span class="lb">门户地址<em v-if="info && !form.issuer.trim()">留空 = 按访问地址</em></span>
          <div class="row">
            <input v-model="form.issuer" spellcheck="false" @keyup.enter="save" />
            <button v-if="info && form.issuer !== info.request_origin" type="button" class="btn"
              @click="form.issuer = info.request_origin">用当前地址</button>
          </div>
        </label>
      </div>

      <h4>已登记的系统<em v-if="info && info.clients.length">{{ info.clients.length }} 个</em></h4>

      <p v-if="info && !info.enabled && info.clients.length" class="warn">
        提供方现在是关着的，下面这些系统都登不进来。
      </p>
      <p v-if="info && !info.clients.length" class="hint">
        还没有。编辑那个系统的导航卡片，把「单点登录」切到开启即可登记。
      </p>
      <ul v-else-if="info" class="list">
        <li v-for="c in info.clients" :key="c.client_id + c.source" :class="{ off: !c.enabled || c.shadowed }">
          <div class="top">
            <span class="name">{{ c.name }}</span>
            <span class="badge" :class="badge(c).cls">{{ badge(c).text }}</span>
          </div>
          <div class="meta">
            <code>{{ c.client_id }}</code>
            <span>{{ c.source === 'conf' ? 'conf.json 登记' : c.owner + ' 的卡片' }}</span>
          </div>
          <div v-for="u in c.redirect_uris" :key="u" class="uri">{{ u }}</div>
        </li>
      </ul>

      <p v-if="error" class="err">{{ error }}</p>
      <p v-if="notice" class="ok">{{ notice }}</p>

      <div class="bottom">
        <button class="btn" @click="emit('close')">关闭</button>
        <button class="btn primary" :disabled="busy || !dirty" @click="save">保存</button>
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
}
.dialog {
  width: 620px;
  max-width: 100%;
  max-height: 90vh;
  overflow: auto;
  padding: 22px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-lg);
}
h3 { margin: 0 0 16px; font-size: 16px; }
h4 {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin: 24px 0 10px;
  font-size: 14px;
}
h4 em { font-style: normal; font-weight: 400; font-size: 12px; color: var(--text-3); }

/* 开关窄、地址宽，并排一行；窄屏再叠起来 */
.grid { display: grid; grid-template-columns: 150px 1fr; gap: 12px; }
.field { display: block; min-width: 0; }
.lb {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 5px;
  font-size: 12px;
  color: var(--text-2);
}
.lb em { font-style: normal; font-size: 11px; color: var(--text-3); }
.row { display: flex; gap: 8px; }
.row input { flex: 1; min-width: 0; }
.row .btn { flex: none; height: 38px; }

.hint { margin: 0; color: var(--text-3); font-size: 12px; line-height: 1.55; }
.warn { margin: 0 0 10px; color: var(--danger); font-size: 12px; }

.list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.list li {
  padding: 11px 13px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.list li.off { opacity: .55; }
.top { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.name { font-weight: 600; font-size: 13.5px; min-width: 0; overflow-wrap: anywhere; }
.badge {
  flex: none;
  padding: 1px 8px;
  border-radius: 20px;
  background: var(--surface-hover);
  color: var(--text-2);
  font-size: 11px;
  white-space: nowrap;
}
.badge.on {
  background: color-mix(in srgb, var(--primary) 15%, transparent);
  color: var(--primary);
}
.badge.bad {
  background: color-mix(in srgb, var(--danger) 15%, transparent);
  color: var(--danger);
}
.meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 10px;
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-2);
}
code {
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text-2);
  font-size: 11.5px;
  word-break: break-all;
}
.uri {
  margin-top: 6px;
  color: var(--text-3);
  font-size: 12px;
  line-height: 1.5;
  word-break: break-all;
}

.err { color: var(--danger); font-size: 13px; margin: 14px 0 0; }
.ok { color: var(--primary); font-size: 13px; margin: 14px 0 0; }
.bottom {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
}
/* 全局 .btn 没有禁用态，而这里「没改动」时保存是灰着的，不标出来看着像能点 */
.bottom .btn:disabled { opacity: .45; cursor: default; filter: none; transform: none; }

@media (max-width: 560px) {
  .grid { grid-template-columns: 1fr; }
}
</style>
