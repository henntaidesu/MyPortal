<script setup>
import { computed, ref, watch } from 'vue'
import { colorOf, initials } from '../utils'
import { peekIcon, loadIcon } from '../icons'

const props = defineProps({
  item: { type: Object, required: true },
  size: { type: Number, default: 52 }
})

const custom = computed(() => (props.item.icon || '').trim())
const isText = computed(() => !!custom.value && !/^(https?:\/\/|data:|\/)/i.test(custom.value))
const text = computed(() => (isText.value ? custom.value : initials(props.item.name)))
const isEmoji = computed(() => /\p{Extended_Pictographic}/u.test(text.value))

const src = ref('')
let seq = 0

function refresh() {
  const mine = ++seq
  if (isText.value) return (src.value = '')
  if (custom.value.startsWith('data:')) return (src.value = custom.value)
  src.value = peekIcon(props.item)          // 命中缓存直接出图，不闪
  loadIcon(props.item).then((got) => {
    if (mine === seq) src.value = got
  })
}

watch(() => [props.item.url, props.item.icon], refresh, { immediate: true })
</script>

<template>
  <div
    class="icon"
    :style="{
      width: size + 'px',
      height: size + 'px',
      background: src ? 'var(--surface-2)' : colorOf(item.name || item.url),
      fontSize: (isEmoji ? size * 0.55 : size * 0.36) + 'px'
    }"
  >
    <img v-if="src" :src="src" alt="" @error="src = ''" />
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
