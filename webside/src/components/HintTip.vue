<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'

/* 标题旁的小感叹号：说明文字收进来，悬停或点一下才展开，不在表单里占行。
   浮层按最近的定位祖先摆放（CardDialog 里是 .lb），宽度随那一栏。
   warn：里面有要人注意的东西（出错、没开），感叹号变红 */
defineProps({ warn: Boolean })

const open = ref(false)
const pinned = ref(false)       // 点开的就钉住，鼠标移走也不收；触屏只有这一种打开方式
const root = ref(null)
let timer = 0

function enter() {
  clearTimeout(timer)
  open.value = true
}
// 从感叹号挪到浮层要经过一道缝，晚一点再收，不然手还没到浮层就没了
function leave() {
  clearTimeout(timer)
  timer = setTimeout(() => { open.value = pinned.value }, 150)
}
function toggle() {
  pinned.value = !pinned.value
  open.value = pinned.value
}
function onDoc(e) {
  if (pinned.value && !root.value?.contains(e.target)) pinned.value = open.value = false
}
onMounted(() => document.addEventListener('mousedown', onDoc))
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDoc)
  clearTimeout(timer)
})
</script>

<template>
  <span ref="root" class="tip" @mouseenter="enter" @mouseleave="leave">
    <button type="button" class="tip-btn" :class="{ warn, on: open }" aria-label="说明"
      @click.prevent.stop="toggle">!</button>
    <!-- 整个控件常放在 <label> 里：浮层上的点击要拦住，否则会被转给输入框 -->
    <span v-if="open" class="tip-pop" @click.prevent><slot /></span>
  </span>
</template>

<style scoped>
.tip { display: inline-flex; align-self: center; }
.tip-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 15px;
  height: 15px;
  border-radius: 50%;
  border: 1px solid var(--text-3);
  color: var(--text-3);
  font-size: 10px;
  font-weight: 700;
  line-height: 1;
  transition: color .15s, border-color .15s;
}
.tip-btn:hover, .tip-btn.on { color: var(--primary); border-color: var(--primary); }
.tip-btn.warn { color: var(--danger); border-color: var(--danger); }
.tip-pop {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  z-index: 5;
  display: block;
  padding: 9px 11px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface);
  box-shadow: var(--shadow-lg);
  color: var(--text-3);
  font-size: 12px;
  line-height: 1.55;
  cursor: auto;
}
</style>
