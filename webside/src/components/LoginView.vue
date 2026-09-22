<script setup>
import { ref, onMounted, computed } from 'vue'
import { auth, login, oidcLogin } from '../auth'
import { ApiError, OfflineError } from '../api'

/* 登录成功不往外 emit：App.vue 是 watch 着 auth.user 进面板的。
   这里 emit 的话，事件正好赶在自己被卸载的同一拍上，收不到。 */
const username = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)
const userInput = ref(null)

const showLocal = computed(() => auth.providers.local)
const showOidc = computed(() => auth.providers.oidc?.enabled)

onMounted(() => {
  /* OIDC 那条路出错时后端是 303 回 `/?oidc_error=...` 的（见 app/routers/auth.py
     的 _fail）：那一路是浏览器的顶层跳转，回 JSON 的话用户只会看到满屏大括号。
     读出来显示掉，然后把查询串从地址栏抹掉——留着的话刷新一次又弹一遍同样的错 */
  const params = new URLSearchParams(location.search)
  const failed = params.get('oidc_error')
  if (failed) {
    error.value = '单点登录失败：' + failed
    params.delete('oidc_error')
    const rest = params.toString()
    history.replaceState(null, '', location.pathname + (rest ? '?' + rest : ''))
  }
  if (showLocal.value) userInput.value?.focus()
})

async function submit() {
  if (busy.value) return
  if (!username.value.trim()) {
    error.value = '请输入用户名'
    return
  }
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
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="wrap">
    <form class="box" @submit.prevent="submit">
      <h1>门户</h1>

      <template v-if="showLocal">
        <label>用户名</label>
        <input ref="userInput" v-model="username" autocomplete="username" spellcheck="false" />

        <label>密码</label>
        <input v-model="password" type="password" autocomplete="current-password" />
      </template>

      <p v-if="error" class="err">{{ error }}</p>

      <button v-if="showLocal" class="btn primary go" type="submit" :disabled="busy">
        {{ busy ? '登录中…' : '登录' }}
      </button>

      <!-- 两条路都开着时中间画一条分隔线；只有单点登录时不画，
           那会儿它是唯一的入口，上面没有东西要跟它分开 -->
      <div v-if="showLocal && showOidc" class="or"><span>或</span></div>

      <button v-if="showOidc" class="btn go sso" type="button" @click="oidcLogin">
        {{ auth.providers.oidc.label || '单点登录' }}
      </button>

      <p v-if="!showLocal && !showOidc" class="err">
        后端没有开放任何登录方式，检查 conf.json 的 auth 和 oidc 两段。
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
h1 { margin: 0 0 6px; font-size: 20px; }
label {
  display: block;
  margin: 14px 0 6px;
  font-size: 12.5px;
  color: var(--text-2);
}
.go { width: 100%; margin-top: 20px; height: 38px; }
.sso { margin-top: 0; }
.err { color: var(--danger); font-size: 13px; margin: 14px 0 0; }

.or {
  position: relative;
  margin: 16px 0 12px;
  text-align: center;
}
.or::before {
  content: '';
  position: absolute;
  top: 50%;
  left: 0;
  right: 0;
  height: 1px;
  background: var(--border);
}
.or span {
  position: relative;
  padding: 0 10px;
  background: var(--surface);
  color: var(--text-3);
  font-size: 12px;
}
</style>
