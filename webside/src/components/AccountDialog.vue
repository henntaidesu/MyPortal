<script setup>
/**
 * 自己改自己：显示名、口令、绑了哪些 IdP、把自己所有设备踢下线。
 *
 * 和 UsersDialog.vue 分开，对着后端那两套接口：这里打 /api/account（只要登录），
 * 那里打 /api/users（要管理员）。混成一个面板的话，普通用户看到的一半按钮
 * 会是点了就 403 的。
 */
import { ref, reactive, onMounted, onBeforeUnmount } from 'vue'
import { api, ApiError } from '../api'
import { auth } from '../auth'

const emit = defineEmits(['close'])

const account = ref(null)
const error = ref('')
const notice = ref('')
const busy = ref(false)

const form = reactive({ display_name: '', old_password: '', new_password: '', confirm: '' })

async function load() {
  try {
    account.value = (await api('/api/account')).account
    form.display_name = account.value.display_name || ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '读不出账号信息'
  }
}

async function saveProfile() {
  if (busy.value) return
  busy.value = true
  error.value = notice.value = ''
  try {
    const res = await api('/api/account', {
      method: 'PATCH', body: { display_name: form.display_name.trim() }
    })
    // 顶栏显示的是 auth.user.display_name，不同步的话改完得刷新页面才看得到
    auth.user = { ...auth.user, display_name: res.account.display_name }
    notice.value = '显示名已保存'
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '保存失败'
  } finally {
    busy.value = false
  }
}

async function changePassword() {
  if (busy.value) return
  if (form.new_password !== form.confirm) {
    error.value = '两次输入的新口令不一样'
    return
  }
  busy.value = true
  error.value = notice.value = ''
  try {
    await api('/api/account/password', {
      method: 'PUT',
      body: { old_password: form.old_password, new_password: form.new_password }
    })
    form.old_password = form.new_password = form.confirm = ''
    // 后端改完口令会顺手换一张新 Cookie，所以这儿不会被踢回登录页
    notice.value = '口令已改。其它设备上的登录都已经失效'
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '改口令失败'
  } finally {
    busy.value = false
  }
}

async function logoutAll() {
  if (!confirm('把所有设备上的登录都踢掉？包括现在这台，你会被退回登录页。')) return
  try {
    await api('/api/account/logout-all', { method: 'POST' })
  } finally {
    auth.user = null            // 退回登录页，App.vue 的 watch 会把自动保存停掉
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
      <h3>账号</h3>

      <div v-if="account" class="who">
        <div class="name">{{ account.username }}</div>
        <div class="meta">
          {{ account.is_admin ? '管理员' : '普通用户' }}
          <template v-if="account.identities.length">
            · 已绑定 {{ account.identities.map(i => i.provider).join('、') }}
          </template>
        </div>
      </div>

      <label class="field">
        <span class="lb">显示名</span>
        <input v-model="form.display_name" placeholder="页面右上角显示的名字" />
      </label>
      <div class="foot">
        <button class="btn" :disabled="busy" @click="saveProfile">保存显示名</button>
      </div>

      <!-- 纯单点登录的账号没有本地口令，这一段整个不画：
           画出来点不动，只会让人以为是坏了 -->
      <template v-if="account && account.has_password">
        <h4>改口令</h4>
        <label class="field">
          <span class="lb">旧口令</span>
          <input v-model="form.old_password" type="password" autocomplete="current-password" />
        </label>
        <label class="field">
          <span class="lb">新口令</span>
          <input v-model="form.new_password" type="password" autocomplete="new-password" />
        </label>
        <label class="field">
          <span class="lb">再输一遍</span>
          <input v-model="form.confirm" type="password" autocomplete="new-password" />
        </label>
        <p class="hint">改完之后，其它设备上的登录会立刻失效。</p>
        <div class="foot">
          <button class="btn primary" :disabled="busy" @click="changePassword">改口令</button>
        </div>
      </template>
      <p v-else-if="account" class="hint">
        这个账号只能走单点登录，没有本地口令。要加一个，找管理员。
      </p>

      <p v-if="error" class="err">{{ error }}</p>
      <p v-if="notice" class="ok">{{ notice }}</p>

      <div class="bottom">
        <button class="btn danger" @click="logoutAll">踢掉所有设备</button>
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
}
.dialog {
  width: 420px;
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
h4 { margin: 22px 0 0; font-size: 13.5px; color: var(--text-2); }
.who {
  padding: 12px;
  margin-bottom: 8px;
  border: 1px dashed var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-2);
}
.name { font-weight: 600; }
.meta { color: var(--text-3); font-size: 12px; margin-top: 2px; }
.field { display: block; margin-top: 12px; }
.lb { display: block; font-size: 12.5px; color: var(--text-2); margin-bottom: 6px; }
.hint { color: var(--text-3); font-size: 12px; margin: 10px 0 0; }
.err { color: var(--danger); font-size: 13px; margin: 12px 0 0; }
.ok { color: var(--primary); font-size: 13px; margin: 12px 0 0; }
.foot { display: flex; justify-content: flex-end; margin-top: 12px; }
.bottom {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-top: 22px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
}
.btn.danger { color: var(--danger); border-color: color-mix(in srgb, var(--danger) 35%, var(--border)); }
</style>
