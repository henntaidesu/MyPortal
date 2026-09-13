<script setup>
import { reactive, ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import NavIcon from './NavIcon.vue'
import { state, addItem, updateItem } from '../store'
import { getHost, normalizeUrl } from '../utils'

const props = defineProps({
  /** 这张卡片所在（或将要加进）的分类 */
  group: { type: Object, required: true },
  item: { type: Object, default: null }
})
const emit = defineEmits(['close'])

const isEdit = computed(() => !!props.item)
const form = reactive({
  name: props.item?.name || '',
  url: props.item?.url || '',
  desc: props.item?.desc || '',
  icon: props.item?.icon || '',
  group: props.group.id          // 改成别的分类就等于把这张卡片搬过去
})

const error = ref('')
const nameInput = ref(null)

// 先填地址没填名字时，用域名兜底
watch(() => form.url, (v) => {
  if (!form.name && v) form.name = getHost(v).replace(/^www\./, '')
})

function submit() {
  if (!form.name.trim()) return (error.value = '请填写名称')
  if (!form.url.trim()) return (error.value = '请填写地址')
  if (isEdit.value) updateItem(props.group.id, props.item.id, form)
  else addItem(form.group, form)
  emit('close')
}

function onKey(e) {
  if (e.key === 'Escape') emit('close')
}
onMounted(() => {
  window.addEventListener('keydown', onKey)
  nameInput.value?.focus()
})
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div class="mask" @mousedown.self="emit('close')">
    <div class="dialog">
      <h3>{{ isEdit ? '编辑导航' : '添加导航' }}</h3>

      <div class="preview">
        <NavIcon :item="form" :size="40" />
        <div class="pv">
          <div class="pv-name">{{ form.name || '未命名' }}</div>
          <div class="pv-url">{{ normalizeUrl(form.url) || '等待填写地址…' }}</div>
        </div>
      </div>

      <!-- 不放 label、也不放 placeholder：从上到下依次是 分类 / 名称 / 地址 / 描述 / 图标。
           title 留着，鼠标停一下还认得出是哪一栏，屏幕阅读器也有 aria-label 可念。 -->
      <select v-model="form.group" title="分类" aria-label="分类">
        <option v-for="g in state.groups" :key="g.id" :value="g.id">{{ g.name }}</option>
      </select>

      <input ref="nameInput" v-model="form.name" title="名称" aria-label="名称" @keyup.enter="submit" />

      <input v-model="form.url" title="地址" aria-label="地址" @keyup.enter="submit" />

      <input v-model="form.desc" title="描述（可选）" aria-label="描述" @keyup.enter="submit" />

      <input v-model="form.icon" title="图标（可选，留空自动抓取）" aria-label="图标" @keyup.enter="submit" />

      <p v-if="error" class="err">{{ error }}</p>

      <div class="foot">
        <button class="btn" @click="emit('close')">取消</button>
        <button class="btn primary" @click="submit">{{ isEdit ? '保存' : '添加' }}</button>
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
  animation: fade .15s;
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
  animation: pop .16s cubic-bezier(.2, .9, .3, 1.2);
}
h3 { margin: 0 0 16px; font-size: 16px; }
.preview {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  margin-bottom: 16px;
  border: 1px dashed var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-2);
}
.pv { min-width: 0; }
.pv-name { font-weight: 600; }
.pv-url {
  color: var(--text-3);
  font-size: 12px;
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* label 去掉了，间距改由输入框自己撑 */
.dialog select,
.dialog input { margin-top: 10px; }
.err { color: var(--danger); font-size: 13px; margin: 12px 0 0; }
.foot { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; }
@keyframes fade { from { opacity: 0 } }
@keyframes pop { from { opacity: 0; transform: translateY(8px) scale(.98) } }
</style>
