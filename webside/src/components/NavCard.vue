<script setup>
import { computed } from 'vue'
import NavIcon from './NavIcon.vue'
import { getHost } from '../utils'

const props = defineProps({ item: { type: Object, required: true } })
const emit = defineEmits(['edit', 'remove'])

const host = computed(() => getHost(props.item.url))
</script>

<template>
  <a class="card" :href="item.url" target="_blank" rel="noopener" :title="item.url">
    <NavIcon :item="item" :size="52" />
    <div class="meta">
      <div class="name">
        <span class="txt">{{ item.name }}</span>
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
