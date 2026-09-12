<script setup>
import { computed, ref, watch } from 'vue'
import { colorOf, initials } from '../utils'
import { iconUrl } from '../icons'

const props = defineProps({
  item: { type: Object, required: true },
  size: { type: Number, default: 52 }
})

const custom = computed(() => (props.item.icon || '').trim())
const isText = computed(() => !!custom.value && !/^(https?:\/\/|data:|\/)/i.test(custom.value))
const text = computed(() => (isText.value ? custom.value : initials(props.item.name)))
const isEmoji = computed(() => /\p{Extended_Pictographic}/u.test(text.value))

/* 直接把 /api/icon 的地址交给 <img>：图缓存在后端，浏览器再按 cache-control 缓一天，
   不用前端自己 fetch 成 dataURL 再塞进来。后端 404（那站点没图标）就走 onerror 退文字徽标 */
const failed = ref(false)
const src = computed(() => (isText.value ? '' : iconUrl(props.item)))
watch(src, () => (failed.value = false))
const shown = computed(() => (failed.value ? '' : src.value))
</script>

<template>
  <div
    class="icon"
    :style="{
      width: size + 'px',
      height: size + 'px',
      background: shown ? 'var(--surface-2)' : colorOf(item.name || item.url),
      fontSize: (isEmoji ? size * 0.55 : size * 0.36) + 'px'
    }"
  >
    <img v-if="shown" :src="shown" alt="" @error="failed = true" />
    <span v-else :class="{ emoji: isEmoji }">{{ text }}</span>
  </div>
</template>

<style scoped>
.icon {
  flex: none;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  color: #fff;
  font-weight: 600;
  user-select: none;
}
img { width: 68%; height: 68%; object-fit: contain; }
.emoji { color: initial; }
</style>
