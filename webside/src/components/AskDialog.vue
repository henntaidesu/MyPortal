<script setup>
/**
 * ask.js 那两个函数的画面，App.vue 挂一个，谁调都画在这儿。
 *
 * 常常是叠在别的弹窗上面开的（用户管理里点「重置密码」），所以两件事要管住：
 *
 * - **z-index 比普通弹窗高一档**，遮罩盖住底下那个弹窗，点不到它的按钮。
 * - **Esc 只关这一层**。底下那些弹窗都在 window 上听 Esc 关自己，这里在**捕获阶段**
 *   先接住再 stopImmediatePropagation，不然按一下 Esc 两层一起没了。
 *
 * 输入框回车即确定，但输入法上屏的那一下回车不算（`isComposing`）：
 * 打中文分类名时按回车选词，不该连着把框提交掉。
 * 不包 `<form>`：带 password 框的表单一提交，浏览器会弹「要保存密码吗」，
 * 存下来的还是一个没有用户名、属于别人的口令。
 */
import { ref, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { current } from '../ask'

const text = ref('')
const input = ref(null)
const okBtn = ref(null)

watch(current, async (req) => {
  if (!req) return
  text.value = req.kind === 'text' ? String(req.value ?? '') : ''
  await nextTick()
  if (req.kind === 'text') {
    input.value?.focus()
    input.value?.select()
  } else {
    // 和浏览器自带的一样，焦点落在确定上，回车就是确定
    okBtn.value?.focus()
  }
})

function ok() {
  const req = current.value
  if (req) req.answer(req.kind === 'text' ? text.value : true)
}

function cancel() {
  const req = current.value
  if (req) req.answer(req.cancelValue)
}

function onEnter(e) {
  if (e.isComposing) return
  e.preventDefault()
  ok()
}

function onKey(e) {
  if (!current.value || e.key !== 'Escape') return
  e.stopImmediatePropagation()
  cancel()
}
onMounted(() => window.addEventListener('keydown', onKey, true))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey, true))
</script>

<template>
  <div v-if="current" class="mask" @mousedown.self="cancel">
    <div class="dialog" role="dialog" aria-modal="true">
      <h3 v-if="current.title">{{ current.title }}</h3>
      <p v-if="current.message" class="msg">{{ current.message }}</p>

      <label v-if="current.kind === 'text'" class="field">
        <span v-if="current.label" class="lb">{{ current.label }}</span>
        <input
          ref="input"
          v-model="text"
          :type="current.type"
          :autocomplete="current.type === 'password' ? 'new-password' : 'off'"
          spellcheck="false"
          @keydown.enter="onEnter"
        />
      </label>

      <div class="foot">
        <button class="btn" @click="cancel">取消</button>
        <button ref="okBtn" class="btn" :class="current.danger ? 'danger' : 'primary'" @click="ok">
          {{ current.ok }}
        </button>
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
  z-index: 60;
}
.dialog {
  width: 400px;
  max-width: 100%;
  padding: 22px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-lg);
}
h3 { margin: 0 0 10px; font-size: 16px; }
.msg {
  margin: 0;
  color: var(--text-2);
  font-size: 13.5px;
  line-height: 1.65;
  white-space: pre-line;
  word-break: break-word;
}
.field { display: block; margin-top: 14px; }
.lb { display: block; font-size: 12.5px; color: var(--text-2); margin-bottom: 6px; }
.foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 20px; }
.btn:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary) 25%, transparent);
}
.btn.danger {
  background: var(--danger);
  border-color: var(--danger);
  color: #fff;
}
.btn.danger:hover { filter: brightness(1.08); }
.btn.danger:focus-visible { box-shadow: 0 0 0 3px color-mix(in srgb, var(--danger) 25%, transparent); }
</style>
