<script setup>
/**
 * 一个分类 = 一张大卡：头上是分类名和几个动作，身子里是这个分类的导航卡片。
 *
 * 拖放的状态在 ../drag.js 里，这个组件只管把事件报上去、按状态加高亮的 class。
 * 分类名用 contenteditable，和页面标题一个路子——为它单开一个弹窗不值当。
 */
import { computed } from 'vue'
import NavCard from './NavCard.vue'
import { renameGroup, groupIndex } from '../store'
import { drag, startCard, startGroup, overCard, overGroup, leaveGroup, dropOnCard, dropOnGroup, end } from '../drag'

const props = defineProps({
  group: { type: Object, required: true }
})
const emit = defineEmits(['add', 'edit', 'remove-item', 'remove-group'])

const gid = computed(() => props.group.id)
const isDropTarget = computed(() => !!drag.kind && drag.overGid === gid.value)
const isDragging = computed(() => drag.kind === 'group' && drag.fromIndex === groupIndex(gid.value))

function onRename(e) {
  renameGroup(gid.value, e.target.innerText)
  // 名字被 store 兜过底（空了会回默认值），把 DOM 里那份也拉回来，免得显示的和存的不一致
  e.target.innerText = props.group.name
}

/**
 * dragleave 会从每个子元素冒上来：鼠标从分类名挪到卡片上也算一次 leave，
 * 不判断的话高亮会一路闪。只有真的离开了整张大卡才清掉。
 */
function onLeave(e) {
  if (!e.currentTarget.contains(e.relatedTarget)) leaveGroup(gid.value)
}
</script>

<template>
  <section
    class="group"
    :class="{ over: isDropTarget, dim: isDragging }"
    @dragover.prevent="overGroup(gid)"
    @dragleave="onLeave"
    @drop.prevent="dropOnGroup(gid)"
  >
    <header class="ghead">
      <span
        class="grip"
        title="拖动调整分类顺序"
        draggable="true"
        @dragstart="startGroup(groupIndex(gid))"
        @dragend="end"
      >⠿</span>

      <h2
        class="gname"
        contenteditable
        spellcheck="false"
        @keydown.enter.prevent="$event.target.blur()"
        @blur="onRename"
      >{{ group.name }}</h2>

      <span class="count">{{ group.items.length }}</span>

      <span class="gacts">
        <button class="gact" title="往这个分类里加一个导航" @click="emit('add', group)">＋</button>
        <button class="gact danger" title="删除这个分类" @click="emit('remove-group', group)">✕</button>
      </span>
    </header>

    <div class="grid">
      <div
        v-for="(it, i) in group.items"
        :key="it.id"
        class="slot"
        :class="{
          over: drag.kind === 'card' && drag.overGid === gid && drag.overIndex === i,
          dragging: drag.kind === 'card' && drag.fromGid === gid && drag.fromIndex === i
        }"
        draggable="true"
        @dragstart="startCard(gid, i)"
        @dragover.prevent.stop="overCard(gid, i)"
        @drop.prevent.stop="dropOnCard(gid, i)"
        @dragend="end"
      >
        <NavCard
          :item="it"
          @edit="emit('edit', { group, item: it })"
          @remove="emit('remove-item', { group, item: it })"
        />
      </div>

      <button class="add" @click="emit('add', group)">
        <span class="plus">＋</span>
        <span>添加导航</span>
      </button>
    </div>
  </section>
</template>

<style scoped>
.group {
  padding: 16px 18px 18px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  transition: box-shadow .15s, border-color .15s, opacity .15s;
}
.group.over { border-color: var(--primary); box-shadow: 0 0 0 2px var(--primary); }
.group.dim { opacity: .45; }

.ghead {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
}
.grip {
  flex: none;
  color: var(--text-3);
  cursor: grab;
  font-size: 15px;
  line-height: 1;
  padding: 2px;
  user-select: none;
}
.grip:active { cursor: grabbing; }

.gname {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  outline: none;
  border-radius: 6px;
  padding: 2px 6px;
  min-width: 24px;
}
.gname:hover { background: var(--surface-hover); }
.gname:focus { background: var(--surface-2); box-shadow: 0 0 0 2px var(--primary); }

.count {
  flex: none;
  padding: 1px 7px;
  border-radius: 20px;
  background: var(--surface-2);
  color: var(--text-3);
  font-size: 11.5px;
}

.gacts { display: flex; gap: 2px; margin-left: auto; opacity: 0; transition: opacity .14s; }
.group:hover .gacts { opacity: 1; }
.gact {
  width: 26px;
  height: 26px;
  border-radius: 7px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  color: var(--text-3);
}
.gact:hover { background: var(--surface-hover); color: var(--text); }
.gact.danger:hover { background: rgba(229, 72, 77, .14); color: var(--danger); }

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px;
}
.slot { border-radius: var(--radius); transition: opacity .15s; }
.slot.dragging { opacity: .35; }
.slot.over { box-shadow: 0 0 0 2px var(--primary); }

.add {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  min-height: 84px;
  border: 1px dashed var(--border);
  border-radius: var(--radius);
  color: var(--text-3);
  font-size: 13px;
  transition: border-color .15s, color .15s, background .15s;
}
.add:hover {
  border-color: var(--primary);
  color: var(--primary);
  background: var(--surface-2);
}
.plus { font-size: 22px; line-height: 1; }

@media (max-width: 600px) {
  .grid { grid-template-columns: 1fr; }
}
</style>
