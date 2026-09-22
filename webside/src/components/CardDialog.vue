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
  // 走不走门户代理：false 关 / true 直接转发 / 'site' 整站改写，见 app/proxy.py
  proxy: props.item?.proxy === 'site' ? 'site' : !!props.item?.proxy,
  proxyHosts: props.item?.proxyHosts || '',
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

      <!-- 每栏都带一行 label：编辑态字段是填好的，placeholder 顶不上来，
           光靠顺序认不出哪栏是哪栏。label 用 <label> 包住控件，点标题也能聚焦。 -->
      <label class="field">
        <span class="lb">分类</span>
        <select v-model="form.group">
          <option v-for="g in state.groups" :key="g.id" :value="g.id">{{ g.name }}</option>
        </select>
      </label>

      <label class="field">
        <span class="lb">名称</span>
        <input ref="nameInput" v-model="form.name" placeholder="例如：GitHub" @keyup.enter="submit" />
      </label>

      <label class="field">
        <span class="lb">地址</span>
        <input v-model="form.url" placeholder="例如：https://github.com" @keyup.enter="submit" />
      </label>

      <!-- 开启之后这张卡片点开的是 /api/proxy/<id>，由门户那个进程转出去。
           门户装在能开那个站的网络里、人在外面时才用得上；本来就够得着的话开着只是多绕一道。
           「直接转发」只转卡片这一台机器，正文一个字不改，内网后台用它最稳；
           「整站」会把页面里的地址一并改写，公网站点（图和接口散在别的域名上）得用它。
           用下拉框而不是勾选框：和上面的「分类」一栏同一个形状，一列对齐下来
           每栏都是「标题 + 一个控件」，不会中间冒出一个跟别人不一样的方块 -->
      <label class="field">
        <span class="lb">门户代理</span>
        <select v-model="form.proxy">
          <option :value="false">关闭</option>
          <option :value="true">开启 · 直接转发（内网后台）</option>
          <option value="site">开启 · 整站（公网站点）</option>
        </select>
      </label>

      <!-- 只有整站模式用得上，所以平时不占位置。站点自己那几个域名后端内置了一份
           （app/proxy_webside/），这里填的是补充：图或接口在别的域名上、又没内置时才要填 -->
      <label v-if="form.proxy === 'site'" class="field">
        <span class="lb">额外域名<em>可选</em></span>
        <input v-model="form.proxyHosts" placeholder="逗号隔开，例如：example-cdn.com" @keyup.enter="submit" />
      </label>

      <label class="field">
        <span class="lb">描述<em>可选</em></span>
        <input v-model="form.desc" placeholder="一句话写清这是什么" @keyup.enter="submit" />
      </label>

      <label class="field">
        <span class="lb">图标<em>可选</em></span>
        <input v-model="form.icon" placeholder="图片地址，留空自动抓取" @keyup.enter="submit" />
      </label>

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
.field { display: block; margin-top: 12px; }
.lb {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 5px;
  font-size: 12px;
  color: var(--text-2);
}
/* 「可选」跟在标题后面，浅一档，跟必填栏一眼分得开 */
.lb em { font-style: normal; font-size: 11px; color: var(--text-3); }
.dialog input::placeholder { color: var(--text-3); }
.err { color: var(--danger); font-size: 13px; margin: 12px 0 0; }
.foot { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; }
@keyframes fade { from { opacity: 0 } }
@keyframes pop { from { opacity: 0; transform: translateY(8px) scale(.98) } }
</style>
