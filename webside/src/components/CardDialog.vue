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

      <label>分类</label>
      <select v-model="form.group">
        <option v-for="g in state.groups" :key="g.id" :value="g.id">{{ g.name }}</option>
      </select>

      <label>名称</label>
      <input ref="nameInput" v-model="form.name" placeholder="例如：运维监控" @keyup.enter="submit" />

      <label>地址</label>
      <input v-model="form.url" placeholder="192.168.1.10:8080 或 https://example.com" @keyup.enter="submit" />

      <label>描述<span class="opt">（可选）</span></label>
      <input v-model="form.desc" placeholder="留空显示域名" @keyup.enter="submit" />

      <label>图标<span class="opt">（可选，留空自动抓取）</span></label>
      <input v-model="form.icon" placeholder="emoji、文字或图片地址" @keyup.enter="submit" />

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
label {
  display: block;
  margin: 12px 0 6px;
  font-size: 12.5px;
  color: var(--text-2);
}
.opt { color: var(--text-3); }
.err { color: var(--danger); font-size: 13px; margin: 12px 0 0; }
.foot { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; }
@keyframes fade { from { opacity: 0 } }
@keyframes pop { from { opacity: 0; transform: translateY(8px) scale(.98) } }
</style>
