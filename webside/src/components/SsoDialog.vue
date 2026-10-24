<script setup>
/**
 * 单点登录提供方的设置：总开关 + 门户地址（issuer），存在库里（app/idp.py 的 settings），
 * 改完即刻生效、不用重启。只有管理员看得到这个入口，真正的拦截在后端 require_admin。
 *
 * 下面那张表只是「现在认哪些系统」的一览：客户端在导航卡片的编辑框里登记，
 * 这里不重复做一套增删改。
 */
import { ref, reactive, onMounted, onBeforeUnmount } from 'vue'
import { api, ApiError } from '../api'

const emit = defineEmits(['close'])

const form = reactive({ enabled: false, issuer: '' })
const info = ref(null)        // 后端回的整份：effective_issuer / request_origin / clients
const error = ref('')
const notice = ref('')
const busy = ref(false)

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
  if (busy.value) return
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

      <label class="field">
        <span class="lb">单点登录提供方</span>
        <select v-model="form.enabled">
          <option :value="false">关闭</option>
          <option :value="true">开启 · 导航里的系统可以用门户账号登录</option>
        </select>
      </label>

      <label class="field">
        <span class="lb">门户地址<em>issuer，浏览器访问门户用的地址</em></span>
        <div class="row">
          <input v-model="form.issuer" spellcheck="false" @keyup.enter="save"
            :placeholder="'留空按访问地址现拼：' + (info?.request_origin || '')" />
          <button v-if="info && form.issuer !== info.request_origin" type="button" class="btn"
            @click="form.issuer = info.request_origin">用当前地址</button>
        </div>
      </label>
      <p class="hint">
        各系统单点登录设置里的「门户地址」必须和它<strong>一字不差</strong>（http/https、端口、末尾不带 /）。
        门户同时有内网、公网两个地址时一定要填，填浏览器实际打开的那个。
        <template v-if="info">现在生效的是 <code>{{ info.effective_issuer }}</code>。</template>
      </p>

      <p v-if="error" class="err">{{ error }}</p>
      <p v-if="notice" class="ok">{{ notice }}</p>

      <div class="foot">
        <button class="btn" @click="emit('close')">关闭</button>
        <button class="btn primary" :disabled="busy" @click="save">保存</button>
      </div>

      <h4>已登记的系统</h4>
      <p v-if="info && !info.clients.length" class="hint">
        还没有。编辑那个系统的导航卡片，把「单点登录」切到开启即可登记。
      </p>
      <table v-else-if="info" class="tbl">
        <thead><tr><th>系统</th><th>Client ID</th><th>来源</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="c in info.clients" :key="c.client_id + c.source" :class="{ off: !c.enabled || c.shadowed }">
            <td>
              <div class="u">{{ c.name }}</div>
              <div v-for="u in c.redirect_uris" :key="u" class="sub">{{ u }}</div>
            </td>
            <td><code>{{ c.client_id }}</code></td>
            <td>{{ c.source === 'conf' ? 'conf.json' : '卡片（' + c.owner + '）' }}</td>
            <td>{{ c.shadowed ? '被 conf.json 同名项覆盖' : c.enabled ? '生效' : '已关闭' }}</td>
          </tr>
        </tbody>
      </table>
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
h3 { margin: 0 0 4px; font-size: 16px; }
h4 { margin: 22px 0 10px; font-size: 14px; }
.field { display: block; margin-top: 12px; }
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
.hint { margin: 8px 0 0; color: var(--text-3); font-size: 12px; line-height: 1.55; }
.hint strong { color: var(--text-2); }
code {
  padding: 0 4px;
  border-radius: 4px;
  background: var(--surface-2);
  color: var(--text-2);
  word-break: break-all;
}
.err { color: var(--danger); font-size: 13px; margin: 12px 0 0; }
.ok { color: var(--primary); font-size: 13px; margin: 12px 0 0; }
.foot { display: flex; justify-content: flex-end; gap: 10px; margin-top: 16px; }

.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th {
  text-align: left;
  font-weight: 500;
  color: var(--text-3);
  font-size: 12px;
  padding: 0 8px 8px 0;
  border-bottom: 1px solid var(--border);
}
.tbl td { padding: 10px 8px 10px 0; border-bottom: 1px solid var(--border); vertical-align: top; }
.tbl tr.off { opacity: .55; }
.u { font-weight: 600; }
.sub { color: var(--text-3); font-size: 12px; margin-top: 2px; word-break: break-all; }
</style>
