<script setup>
import { ref, onMounted } from 'vue'
import { auth, login } from '../auth'

const props = defineProps({
  /** 登录后要去的站内地址，来自 ?next=；为空就是直接进门户 */
  next: { type: String, default: '' }
})
const emit = defineEmits(['done'])

const username = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)
const userInput = ref(null)

/* 登录页上还没有登录态，拿不到系统名字，只能从 next 里认出 client_id */
const targetId = (/[?&]client_id=([^&]+)/.exec(props.next) || [])[1]
const target = targetId ? decodeURIComponent(targetId) : ''

async function submit() {
  if (busy.value) return
  if (!username.value.trim()) return (error.value = '请输入用户名')
  if (!password.value) return (error.value = '请输入密码')

  busy.value = true
  error.value = ''
  try {
    await login(username.value.trim(), password.value)
    password.value = ''
    emit('done')
  } catch (err) {
    error.value = err.message
    password.value = ''
  } finally {
    busy.value = false
  }
}

onMounted(() => userInput.value?.focus())
</script>

<template>
  <div class="wrap">
    <form class="card" @submit.prevent="submit">
      <h1>登录</h1>
      <p class="sub">
        <template v-if="target">登录后直接进入 <code>{{ target }}</code>，那边不用再登一次</template>
        <template v-else>登录后打开导航面板</template>
      </p>

      <label for="u">用户名</label>
      <input id="u" ref="userInput" v-model="username" autocomplete="username" spellcheck="false" />

      <label for="p">密码</label>
      <input id="p" v-model="password" type="password" autocomplete="current-password" />

      <p v-if="error" class="err">{{ error }}</p>
      <p v-else-if="auth.offline" class="err">连不上认证服务，请先启动后端（backend 目录）</p>

      <button class="btn primary submit" type="submit" :disabled="busy">
        {{ busy ? '登录中…' : '登 录' }}
      </button>

      <p class="hint">忘记密码找管理员，在 backend 目录执行 <code>python manage.py passwd 用户名</code></p>
    </form>
  </div>
</template>

<style scoped>
.wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.card {
  width: 360px;
  max-width: 100%;
  padding: 30px 28px 24px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-lg);
}
h1 { margin: 0; font-size: 20px; }
.sub { margin: 8px 0 22px; color: var(--text-3); font-size: 13px; line-height: 1.6; }
label {
  display: block;
  margin: 14px 0 6px;
  font-size: 12.5px;
  color: var(--text-2);
}
.err { color: var(--danger); font-size: 13px; margin: 14px 0 0; }
.submit {
  width: 100%;
  height: 40px;
  margin-top: 22px;
  letter-spacing: 2px;
}
.submit:disabled { opacity: .6; cursor: default; }
.hint {
  margin: 18px 0 0;
  color: var(--text-3);
  font-size: 11.5px;
  line-height: 1.7;
  text-align: center;
}
code {
  background: var(--surface-2);
  border-radius: 4px;
  padding: 1px 5px;
  font-size: 11px;
}
</style>
