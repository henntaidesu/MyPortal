<script setup>
import { ref, onMounted } from 'vue'
import { auth, login } from '../auth'
import { ApiError, OfflineError } from '../api'

/* 登录成功不往外 emit：App.vue 是 watch 着 auth.user 进面板的。
   这里 emit 的话，事件正好赶在自己被卸载的同一拍上，收不到。 */
const username = ref('admin')
const password = ref('')
const error = ref('')
const busy = ref(false)
const pwInput = ref(null)

/* 用户名多半就是默认的那个，光标直接落在口令上 */
onMounted(() => pwInput.value?.focus())

async function submit() {
  if (busy.value) return
  if (!password.value) {
    error.value = '请输入密码'
    return
  }
  busy.value = true
  error.value = ''
  try {
    await login(username.value.trim(), password.value)
  } catch (err) {
    // 连不上后端和密码错要分开说，不然只会让人一遍遍重敲密码
    error.value = err instanceof OfflineError
      ? '连不上后端，确认它已经启动'
      : (err instanceof ApiError ? err.message : '登录失败，请重试')
    password.value = ''
    pwInput.value?.focus()
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="wrap">
    <form class="box" @submit.prevent="submit">
      <h1>门户</h1>
      <p class="sub">登录后进入导航面板</p>

      <label>用户名</label>
      <input v-model="username" autocomplete="username" spellcheck="false" />

      <label>密码</label>
      <input ref="pwInput" v-model="password" type="password" autocomplete="current-password" />

      <p v-if="error" class="err">{{ error }}</p>

      <button class="btn primary go" type="submit" :disabled="busy">
        {{ busy ? '登录中…' : '登录' }}
      </button>

      <p v-if="auth.defaultPassword" class="warn">
        还在用默认口令 admin / admin。改掉它：编辑后端的
        <code>conf.json</code> → <code>auth.password</code>，然后重启。
      </p>
    </form>
  </div>
</template>

<style scoped>
.wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 20px;
}
.box {
  width: 340px;
  max-width: 100%;
  padding: 30px 28px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-lg);
}
h1 { margin: 0; font-size: 20px; }
.sub { margin: 6px 0 20px; color: var(--text-3); font-size: 13px; }
label {
  display: block;
  margin: 14px 0 6px;
  font-size: 12.5px;
  color: var(--text-2);
}
.go { width: 100%; margin-top: 20px; height: 38px; }
.err { color: var(--danger); font-size: 13px; margin: 14px 0 0; }
.warn {
  margin: 18px 0 0;
  padding-top: 14px;
  border-top: 1px solid var(--border);
  color: var(--text-3);
  font-size: 11.5px;
  line-height: 1.8;
}
.warn code {
  background: var(--surface-2);
  border-radius: 4px;
  padding: 1px 5px;
}
</style>
