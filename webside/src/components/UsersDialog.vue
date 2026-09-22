<script setup>
/**
 * 用户管理，只有管理员看得到这个入口（App.vue 按 auth.user.is_admin 决定画不画）。
 *
 * **前端这份判断不是权限**：真正的拦截在后端（app/routers/users.py 挂的
 * require_admin），这里只是别让人对着一堆注定 403 的按钮点。
 *
 * 「最后一个管理员不能降级 / 停用 / 删除」那几条也在后端（app/users.py），
 * 这里不重复判一遍——两头各写一套的话，改了一头忘了另一头，表现成
 * 「按钮是灰的但其实能点」或者反过来。后端回什么错就显示什么错。
 */
import { ref, reactive, onMounted, onBeforeUnmount } from 'vue'
import { api, ApiError } from '../api'
import { auth } from '../auth'

const emit = defineEmits(['close'])

const rows = ref([])
const error = ref('')
const notice = ref('')
const busy = ref(false)
const adding = ref(false)

const form = reactive({ username: '', password: '', display_name: '', role: 'user' })

async function load() {
  try {
    rows.value = (await api('/api/users')).users
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '读不出用户列表'
  }
}

async function run(fn, done) {
  if (busy.value) return
  busy.value = true
  error.value = notice.value = ''
  try {
    await fn()
    notice.value = done
    await load()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '操作失败'
  } finally {
    busy.value = false
  }
}

function create() {
  if (!form.username.trim()) return (error.value = '请填写用户名')
  run(async () => {
    await api('/api/users', {
      method: 'POST',
      body: {
        username: form.username.trim(),
        // 留空 = 纯单点登录用户，后端存的是 NULL，本地登录那条路对他关着
        password: form.password ? form.password : null,
        display_name: form.display_name.trim(),
        role: form.role
      }
    })
    form.username = form.password = form.display_name = ''
    form.role = 'user'
    adding.value = false
  }, '用户已建好')
}

function toggleRole(u) {
  const next = u.role === 'admin' ? 'user' : 'admin'
  run(() => api(`/api/users/${u.id}`, { method: 'PATCH', body: { role: next } }),
      `${u.username} 现在是${next === 'admin' ? '管理员' : '普通用户'}`)
}

function toggleActive(u) {
  run(() => api(`/api/users/${u.id}`, { method: 'PATCH', body: { is_active: !u.is_active } }),
      `${u.username} 已${u.is_active ? '停用' : '启用'}`)
}

function resetPassword(u) {
  const pw = prompt(`给「${u.username}」设一个新口令（至少 6 位，留空=取消本地口令，只能走单点登录）`)
  if (pw === null) return
  run(() => api(`/api/users/${u.id}/password`, { method: 'PUT', body: { password: pw || null } }),
      `${u.username} 的口令已重置，他所有设备上的登录都失效了`)
}

function kick(u) {
  run(() => api(`/api/users/${u.id}/logout`, { method: 'POST' }),
      `${u.username} 已被踢下线`)
}

function remove(u) {
  if (!confirm(`删除「${u.username}」？他名下 ${u.items} 个导航会一起删掉，删了就没了。`)) return
  run(() => api(`/api/users/${u.id}`, { method: 'DELETE' }), `${u.username} 已删除`)
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
      <h3>用户管理</h3>

      <table class="tbl">
        <thead>
          <tr><th>用户</th><th>角色</th><th>状态</th><th>导航</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="u in rows" :key="u.id" :class="{ off: !u.is_active }">
            <td>
              <div class="u">{{ u.username }}<span v-if="u.id === auth.user.id" class="me">（我）</span></div>
              <div class="sub">
                {{ u.display_name }}
                <template v-if="!u.has_password">· 仅单点登录</template>
                <template v-else-if="u.identities">· 已绑 SSO</template>
              </div>
            </td>
            <td>{{ u.role === 'admin' ? '管理员' : '普通' }}</td>
            <td>{{ u.is_active ? '正常' : '已停用' }}</td>
            <td>{{ u.items }}</td>
            <td class="ops">
              <button class="mini" :disabled="busy" @click="toggleRole(u)">
                {{ u.role === 'admin' ? '降为普通' : '设为管理员' }}
              </button>
              <button class="mini" :disabled="busy" @click="toggleActive(u)">
                {{ u.is_active ? '停用' : '启用' }}
              </button>
              <button class="mini" :disabled="busy" @click="resetPassword(u)">重置口令</button>
              <button class="mini" :disabled="busy" @click="kick(u)">踢下线</button>
              <button class="mini danger" :disabled="busy || u.id === auth.user.id"
                      @click="remove(u)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>

      <div v-if="adding" class="add">
        <label class="field">
          <span class="lb">用户名</span>
          <input v-model="form.username" spellcheck="false" placeholder="字母、数字和 . _ @ -" />
        </label>
        <label class="field">
          <span class="lb">口令</span>
          <input v-model="form.password" type="password" autocomplete="new-password"
                 placeholder="留空 = 只能走单点登录" />
        </label>
        <label class="field">
          <span class="lb">显示名</span>
          <input v-model="form.display_name" placeholder="留空就用用户名" />
        </label>
        <label class="field">
          <span class="lb">角色</span>
          <select v-model="form.role">
            <option value="user">普通用户</option>
            <option value="admin">管理员</option>
          </select>
        </label>
        <div class="foot">
          <button class="btn" @click="adding = false">取消</button>
          <button class="btn primary" :disabled="busy" @click="create">建号</button>
        </div>
      </div>

      <p v-if="error" class="err">{{ error }}</p>
      <p v-if="notice" class="ok">{{ notice }}</p>

      <div class="bottom">
        <button v-if="!adding" class="btn" @click="adding = true">新建用户</button>
        <span v-else></span>
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
  width: 720px;
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
.me { color: var(--text-3); font-weight: 400; font-size: 12px; }
.sub { color: var(--text-3); font-size: 12px; margin-top: 2px; }
.ops { display: flex; flex-wrap: wrap; gap: 4px; justify-content: flex-end; }
.mini {
  padding: 3px 8px;
  font-size: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--text-2);
}
.mini:hover:not(:disabled) { background: var(--surface-hover); }
.mini:disabled { opacity: .4; }
.mini.danger { color: var(--danger); }

.add {
  margin-top: 16px;
  padding: 14px;
  border: 1px dashed var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-2);
}
.field { display: block; margin-top: 10px; }
.field:first-child { margin-top: 0; }
.lb { display: block; font-size: 12.5px; color: var(--text-2); margin-bottom: 6px; }
.foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }

.err { color: var(--danger); font-size: 13px; margin: 12px 0 0; }
.ok { color: var(--primary); font-size: 13px; margin: 12px 0 0; }
.bottom {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
}

@media (max-width: 700px) {
  .ops { justify-content: flex-start; }
}
</style>
