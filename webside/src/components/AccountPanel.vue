<script setup>
/**
 * 「我的账号」页：改自己的用户名、显示名、邮箱、密码。
 *
 * 没有保存按钮，改完就存：文本框在 change（失焦或回车）时提交。用 change 不用
 * 每敲一下就发，是因为用户名要真写进库——按键提交的话「zhangsan」会先被存成「z」「zh」。
 * 密码特殊：两个框填的一样、且够 6 位才提交，不然一失焦就把半截密码存进去了。
 *
 * 改用户名不验当前密码（和 /api/password 一致，见 backend/app/routers/auth.py 里的说明）。
 * 改完不掉线，导航也不会丢——会话和 nav 表记的都是 user id，不是用户名。
 */
import { reactive, ref, computed } from 'vue'
import { auth, updateProfile, changePassword } from '../auth'

const profile = reactive({
  username: auth.user.username,
  display_name: auth.user.display_name,
  email: auth.user.email || ''
})
const pw = reactive({ value: '', repeat: '' })

const error = ref('')
const flash = ref({})          // 字段名 -> 'ok' | 'err'，存完闪一下边框
const roles = computed(() => auth.user.roles.join(', '))

function mark(field, kind) {
  flash.value[field] = kind
  setTimeout(() => {
    if (flash.value[field] === kind) delete flash.value[field]
  }, 1400)
}

/** 改了的字段才提交，一个都没改就什么都不做 */
async function save(field) {
  const body = {}
  if (field === 'username') {
    if (!profile.username.trim() || profile.username.trim() === auth.user.username) return
    body.username = profile.username.trim()
  }
  if (field === 'display_name') {
    if (profile.display_name.trim() === auth.user.display_name) return
    body.display_name = profile.display_name.trim()
  }
  if (field === 'email') {
    if (profile.email.trim() === (auth.user.email || '')) return
    body.email = profile.email.trim()
  }
  if (!Object.keys(body).length) return

  error.value = ''
  try {
    await updateProfile(body)
    mark(field, 'ok')
  } catch (e) {
    error.value = e.message
    mark(field, 'err')
  }
}

async function savePassword() {
  if (pw.value.length < 6 || pw.value !== pw.repeat) return   // 还没填齐，等下一次
  error.value = ''
  try {
    await changePassword(pw.value)
    pw.value = pw.repeat = ''
    mark('password', 'ok')
  } catch (e) {
    error.value = e.message
    mark('password', 'err')
  }
}
</script>

<template>
  <div class="row" :class="flash.username">
    <label for="a-user">用户名</label>
    <input id="a-user" v-model="profile.username" spellcheck="false"
           autocomplete="username" @change="save('username')" />
  </div>

  <div class="row" :class="flash.display_name">
    <label for="a-name">显示名</label>
    <input id="a-name" v-model="profile.display_name" @change="save('display_name')" />
  </div>

  <div class="row" :class="flash.email">
    <label for="a-mail">邮箱</label>
    <input id="a-mail" v-model="profile.email" autocomplete="email" @change="save('email')" />
  </div>

  <div class="row">
    <label for="a-role">角色</label>
    <input id="a-role" :value="roles" disabled />
  </div>

  <div class="row" :class="flash.password">
    <label for="a-new">新密码</label>
    <input id="a-new" v-model="pw.value" type="password"
           autocomplete="new-password" @change="savePassword" />
  </div>

  <div class="row" :class="flash.password">
    <label for="a-rep">再输一次</label>
    <input id="a-rep" v-model="pw.repeat" type="password"
           autocomplete="new-password" @change="savePassword" />
  </div>

  <p v-if="error" class="err">{{ error }}</p>
</template>

<style scoped>
.row {
  padding: 13px 0;
  border-top: 1px solid var(--border);
}
.row:first-child { border-top: none; }
label {
  display: block;
  margin-bottom: 8px;
  font-size: 13.5px;
  font-weight: 600;
}
input:disabled { color: var(--text-3); cursor: default; }

/* 存完闪一下边框，替掉原来的「已保存」字样 */
.row.ok input { border-color: var(--ok, #2e9e5b); }
.row.err input { border-color: var(--danger); }

.err { color: var(--danger); font-size: 13px; margin: 14px 0 0; }
</style>
