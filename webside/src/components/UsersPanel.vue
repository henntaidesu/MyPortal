<script setup>
/**
 * 「用户管理」页：建号、改名、改资料和角色、重置密码、停用、删除。
 *
 * 编辑没有保存按钮，字段在 change（失焦或回车）时提交。用 change 不用按键提交，
 * 是因为改名会真写进库——按键提交的话「zhangsan」会先被存成「z」「zh」，
 * 而用户名是业务系统认人的标识。重置密码要两个框填得一样才提交，
 * 不然一失焦就把半截密码存给别人了。建号是「无中生有」，留着创建按钮。
 *
 * 界面上挡掉的那几下（停用/删除自己、动最后一个管理员）后端也各挡了一道，
 * 这里只是不摆点不动的按钮。真被锁在门外了，命令行 manage.py 那套始终能用。
 */
import { ref, reactive, computed, onMounted } from 'vue'
import { api } from '../api'
import { auth } from '../auth'

const users = ref([])
const loading = ref(true)
const error = ref('')
const busy = ref('')             // 正在处理哪个用户名，处理期间把这一行的按钮禁掉
const flash = ref({})            // 字段名 -> 'ok' | 'err'

const form = ref(null)           // { mode: 'create' | 'edit', origin, ... }
const pwFor = reactive({ username: '', value: '', repeat: '' })

const me = computed(() => auth.user.username.toLowerCase())
/* 还启用着的管理员只剩一个时，界面上先把动他的按钮收起来 */
const admins = computed(() => users.value.filter((u) => u.roles.includes('admin') && !u.disabled))

function isMe(u) {
  return u.username.toLowerCase() === me.value
}
function lastAdmin(u) {
  return admins.value.length === 1 && admins.value[0].username === u.username
}

function mark(field, kind) {
  flash.value[field] = kind
  setTimeout(() => {
    if (flash.value[field] === kind) delete flash.value[field]
  }, 1400)
}

async function reload() {
  try {
    const data = await api('/api/users')
    users.value = data.users
    error.value = ''
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

/* 正在编辑的那个人在列表里的那一行，拿来判断字段到底改没改 */
function row() {
  return users.value.find((u) => u.username === form.value?.origin)
}

function openCreate() {
  pwFor.username = ''
  form.value = {
    mode: 'create', origin: '',
    username: '', display_name: '', email: '', roles: '', password: ''
  }
}

function openEdit(u) {
  pwFor.username = ''
  form.value = {
    mode: 'edit', origin: u.username,
    username: u.username, display_name: u.display_name, email: u.email || '',
    roles: u.roles.join(','), password: ''
  }
}

/** 编辑态：某个字段失焦，改过才提交 */
async function saveField(field) {
  const f = form.value
  const now = row()
  if (!now) return
  const body = {}
  if (field === 'username') {
    if (!f.username.trim() || f.username.trim() === f.origin) return
    body.username = f.username.trim()
  }
  if (field === 'display_name') {
    if (f.display_name.trim() === now.display_name) return
    body.display_name = f.display_name.trim()
  }
  if (field === 'email') {
    if (f.email.trim() === (now.email || '')) return
    body.email = f.email.trim()
  }
  if (field === 'roles') {
    if (f.roles.trim() === now.roles.join(',')) return
    body.roles = f.roles.trim()
  }
  if (!Object.keys(body).length) return

  error.value = ''
  busy.value = f.origin
  try {
    const res = await api('/api/users/' + encodeURIComponent(f.origin), { method: 'PATCH', body })
    f.origin = res.username        // 改了名，后面几下要打到新名字上
    // 改的是自己：顶上那块和自己的角色都得跟着变，重新问一次后端最省事
    if (res.username.toLowerCase() === me.value) auth.user = (await api('/api/me')).user
    await reload()
    mark(field, 'ok')
  } catch (e) {
    error.value = e.message
    mark(field, 'err')
  } finally {
    busy.value = ''
  }
}

async function create() {
  const f = form.value
  error.value = ''
  if (!f.username.trim() || f.password.length < 6) return
  busy.value = f.username
  try {
    await api('/api/users', {
      method: 'POST',
      body: {
        username: f.username.trim(),
        password: f.password,
        display_name: f.display_name.trim(),
        email: f.email.trim(),
        roles: f.roles.trim()
      }
    })
    form.value = null
    await reload()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = ''
  }
}

async function toggle(u) {
  error.value = ''
  busy.value = u.username
  try {
    await api('/api/users/' + encodeURIComponent(u.username), {
      method: 'PATCH',
      body: { disabled: !u.disabled }
    })
    await reload()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = ''
  }
}

async function remove(u) {
  if (!confirm('删除用户「' + u.username + '」？这一步不能撤销。')) return
  error.value = ''
  busy.value = u.username
  try {
    await api('/api/users/' + encodeURIComponent(u.username), { method: 'DELETE' })
    if (form.value?.origin === u.username) form.value = null
    await reload()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = ''
  }
}

function openReset(u) {
  form.value = null
  pwFor.username = u.username
  pwFor.value = ''
  pwFor.repeat = ''
}

/** 两个框填得一样、够 6 位才提交，不然失焦就把半截密码存给别人了 */
async function saveReset() {
  if (pwFor.value.length < 6 || pwFor.value !== pwFor.repeat) return
  error.value = ''
  busy.value = pwFor.username
  try {
    await api('/api/users/' + encodeURIComponent(pwFor.username) + '/password', {
      method: 'PUT',
      body: { new_password: pwFor.value }
    })
    pwFor.username = ''
    pwFor.value = pwFor.repeat = ''
  } catch (e) {
    error.value = e.message
    mark('reset', 'err')
  } finally {
    busy.value = ''
  }
}

onMounted(reload)
</script>

<template>
  <p v-if="loading" class="muted">正在读取…</p>

  <div v-for="u in users" :key="u.username" class="row" :class="{ off: u.disabled }">
    <div class="info">
      <div class="line">
        <span class="name">{{ u.username }}</span>
        <span v-if="isMe(u)" class="tag self">我</span>
        <span v-if="u.roles.includes('admin')" class="tag admin">管理员</span>
        <span v-if="u.disabled" class="tag off">已停用</span>
      </div>
      <div class="sub">
        {{ u.display_name || u.username }}
        <template v-if="u.email"> · {{ u.email }}</template>
        <template v-if="u.roles.length"> · {{ u.roles.join(',') }}</template>
      </div>
    </div>

    <div class="ops">
      <button class="btn sm" :disabled="busy === u.username" @click="openEdit(u)">编辑</button>
      <button class="btn sm" :disabled="busy === u.username" @click="openReset(u)">重置密码</button>
      <button
        class="btn sm"
        :disabled="busy === u.username || isMe(u) || (!u.disabled && lastAdmin(u))"
        @click="toggle(u)"
      >{{ u.disabled ? '启用' : '停用' }}</button>
      <button
        class="btn sm danger"
        :disabled="busy === u.username || isMe(u) || lastAdmin(u)"
        @click="remove(u)"
      >删除</button>
    </div>
  </div>

  <div v-if="pwFor.username" class="panel" :class="flash.reset">
    <h4>{{ pwFor.username }}</h4>

    <label>新密码</label>
    <input v-model="pwFor.value" type="password" autocomplete="new-password" @change="saveReset" />

    <label>再输一次</label>
    <input v-model="pwFor.repeat" type="password" autocomplete="new-password" @change="saveReset" />

    <div class="act">
      <button class="btn" @click="pwFor.username = ''">关闭</button>
    </div>
  </div>

  <div v-if="form" class="panel">
    <h4>{{ form.mode === 'create' ? '新建用户' : form.origin }}</h4>

    <label>用户名</label>
    <input
      v-model="form.username"
      spellcheck="false"
      :class="flash.username"
      @change="form.mode === 'edit' && saveField('username')"
    />

    <label>显示名</label>
    <input
      v-model="form.display_name"
      :class="flash.display_name"
      @change="form.mode === 'edit' && saveField('display_name')"
    />

    <label>邮箱</label>
    <input
      v-model="form.email"
      :class="flash.email"
      @change="form.mode === 'edit' && saveField('email')"
    />

    <label>角色</label>
    <input
      v-model="form.roles"
      spellcheck="false"
      :class="flash.roles"
      @change="form.mode === 'edit' && saveField('roles')"
    />

    <template v-if="form.mode === 'create'">
      <label>初始密码</label>
      <input v-model="form.password" type="password" autocomplete="new-password" @keyup.enter="create" />
    </template>

    <div class="act">
      <button v-if="form.mode === 'create'" class="btn primary" :disabled="!!busy" @click="create">
        创建
      </button>
      <button class="btn" @click="form = null">关闭</button>
    </div>
  </div>

  <button v-else-if="!pwFor.username" class="add" @click="openCreate">＋ 新建用户</button>

  <p v-if="error" class="err">{{ error }}</p>
</template>

<style scoped>
.muted { color: var(--text-3); font-size: 13px; }

.row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 12px 0;
  border-top: 1px solid var(--border);
}
.row:first-child { border-top: none; }
.row.off { opacity: .55; }
.info { flex: 1 1 200px; min-width: 0; }
.line { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.name { font-size: 13.5px; font-weight: 600; }
.sub { margin-top: 3px; color: var(--text-3); font-size: 12px; }

.tag {
  font-size: 11px;
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 1px 8px;
  color: var(--text-3);
}
.tag.admin { color: var(--primary); border-color: var(--primary); }
.tag.self { color: var(--text-2); }
.tag.off { color: var(--danger); border-color: var(--danger); }

.ops { display: flex; gap: 6px; flex-wrap: wrap; }
.btn.sm { height: 30px; padding: 0 10px; font-size: 12.5px; }
.btn.sm:disabled { opacity: .45; cursor: default; }
.btn.danger:not(:disabled):hover { color: var(--danger); border-color: var(--danger); }

.panel {
  margin-top: 14px;
  padding: 14px 16px 16px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
h4 { margin: 0 0 4px; font-size: 13.5px; }
label {
  display: block;
  margin: 12px 0 6px;
  font-size: 12.5px;
  color: var(--text-2);
}
.act { display: flex; gap: 10px; margin-top: 16px; }

/* 存完闪一下边框，替掉原来的「已保存」字样 */
input.ok { border-color: var(--ok, #2e9e5b); }
input.err, .panel.err input { border-color: var(--danger); }

.add {
  width: 100%;
  margin-top: 14px;
  padding: 11px;
  border: 1px dashed var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-3);
  font-size: 13px;
  transition: border-color .15s, color .15s;
}
.add:hover { border-color: var(--primary); color: var(--primary); }

.err { color: var(--danger); font-size: 13px; margin: 14px 0 0; }
</style>
