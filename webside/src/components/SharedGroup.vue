<script setup>
/**
 * 管理员分享给我的卡片，画成一张和分类一样的大卡，但**只读**：
 * 不能拖、不能改名、不能往里加、卡片上没有编辑和删除——分享也只有分享的人收得回去。
 *
 * 不复用 NavGroup.vue 加一个 readonly 开关：那边从头到尾挂着拖放和改名，
 * 每一处都要加条件，漏一处就能把别人的卡片拖进自己的分类里（变成一份拷贝存下去）。
 * 数据来自 store.js 的 shared，不在 state 里，见那边的说明。
 */
import NavCard from './NavCard.vue'
import { shared } from '../store'
</script>

<template>
  <section v-if="shared.length" class="group">
    <header class="ghead">
      <h2 class="gname">分享给我的</h2>
      <span class="count">{{ shared.length }}</span>
    </header>

    <div class="grid">
      <NavCard v-for="it in shared" :key="it.id" :item="it" readonly />
    </div>
  </section>
</template>

<style scoped>
/* 和 NavGroup.vue 同一个样子，只留看得见的那几项 */
.group {
  padding: 16px 18px 18px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
}

.ghead {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
  min-height: 26px;
}

.gname {
  margin: 0;
  padding: 2px 6px;
  font-size: 15px;
  font-weight: 600;
}

.count {
  flex: none;
  padding: 1px 7px;
  border-radius: 20px;
  background: var(--surface-2);
  color: var(--text-3);
  font-size: 11.5px;
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px;
}

@media (max-width: 600px) {
  .grid { grid-template-columns: 1fr; }
}
</style>
