<script setup>
import { computed } from 'vue'
import NavIcon from './NavIcon.vue'
import { getHost, proxyUrl } from '../utils'

const props = defineProps({ item: { type: Object, required: true } })
const emit = defineEmits(['edit', 'remove'])

const host = computed(() => getHost(props.item.url))

/* 开了门户代理就点去 /api/proxy/<id>，由后端转出去（见 app/proxy.py）。
   原地址不显示在链接上了，但 title 里照样写着——不然鼠标停上去看不出这张卡片指着哪台机器 */
const href = computed(() => (props.item.proxy ? proxyUrl(props.item) : props.item.url))
/* 两种代理模式点开的东西不一样，标签上分得开：出问题时先看这张卡片走的是哪一种 */
const mode = computed(() => (props.item.proxy === 'site' ? '整站' : '代理'))
const hint = computed(() =>
  props.item.proxy ? `${props.item.url}（经门户代理·${mode.value}）` : props.item.url)
</script>

<template>
  <a class="card" :href="href" target="_blank" rel="noopener" :title="hint">
    <NavIcon :item="item" :size="52" />
    <div class="meta">
      <div class="name">
        <span class="txt">{{ item.name }}</span>
        <!-- 走不走代理在页面上得看得出来：两者点开的地址不是一回事，出问题时
             第一件要确认的就是这个 -->
        <span v-if="item.proxy" class="tag" :title="hint">{{ mode }}</span>
      </div>
      <div class="desc">{{ item.desc || host }}</div>
    </div>
    <span class="acts" @click.prevent.stop>
      <span class="act" title="编辑" @click="emit('edit', item)">✎</span>
      <span class="act danger" title="删除" @click="emit('remove', item)">✕</span>
    </span>
  </a>
</template>

<style scoped>
.card {
  position: relative;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 18px 20px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  transition: transform .14s, box-shadow .14s, border-color .14s;
}
.card:hover {
  transform: translateY(-2px);
  border-color: var(--primary);
  box-shadow: var(--shadow-lg);
}
.meta { min-width: 0; flex: 1; }
.name {
  display: flex;
  align-items: center;
  gap: 7px;
  font-weight: 600;
  font-size: 16px;
  white-space: nowrap;
}
.txt { overflow: hidden; text-overflow: ellipsis; }
.tag {
  flex: none;
  padding: 1px 6px;
  border-radius: 5px;
  background: color-mix(in srgb, var(--primary) 14%, transparent);
  color: var(--primary);
  font-size: 11px;
  font-weight: 500;
}
.desc {
  margin-top: 5px;
  color: var(--text-3);
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.acts {
  position: absolute;
  right: 8px;
  top: 8px;
  display: flex;
  gap: 2px;
  opacity: 0;
  transition: opacity .14s;
}
.card:hover .acts { opacity: 1; }
.act {
  width: 26px;
  height: 26px;
  border-radius: 7px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: var(--text-3);
  cursor: pointer;
}
.act:hover { background: var(--surface-hover); color: var(--text); }
.act.danger:hover { background: rgba(229, 72, 77, .14); color: var(--danger); }
</style>
