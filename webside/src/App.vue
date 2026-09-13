<script setup>
import { ref, watch, onMounted } from 'vue'
import NavGroup from './components/NavGroup.vue'
import CardDialog from './components/CardDialog.vue'
import LoginView from './components/LoginView.vue'
import { state, removeItem, addGroup, removeGroup, boot, unbind } from './store'
import { end as endDrag } from './drag'
import { auth, refresh } from './auth'

const dialog = ref(null)   // { group, item } | null，item 为 null 表示新增

function askRemoveItem({ group, item }) {
  if (confirm('删除「' + item.name + '」？')) removeItem(group.id, item.id)
}

function askRemoveGroup(group) {
  const n = group.items.length
  const tip = n
    ? `删除分类「${group.name}」？里面 ${n} 个导航会一起删掉。`
    : `删除分类「${group.name}」？`
  if (confirm(tip)) removeGroup(group.id)
}

function newGroup() {
  const name = prompt('新分类叫什么？', '新分类')
  if (name !== null) addGroup(name)
}

/**
 * 进出登录统一由 auth.user 驱动，一处管三种情况：开页面时已经登着、刚登录、会话过期。
 *
 * **别改回「LoginView 登录成功后 emit 一下，App 收到再 boot()」**：登录成功那一刻
 * auth.user 被赋值，Vue 随即把 LoginView 卸载掉，emit 正好赶在自己被卸载的同一拍上，
 * 事件就丢了——表现是登录后页面进来了，但后端一个 /api/nav 请求都没发出去，
 * 改的东西全只活在内存里。
 */
watch(() => auth.user, (user) => {
  if (user) boot()       // 自己处理错误，不用 await
  else unbind()          // 会话过期：停掉自动保存，不然它会对着 401 一直重试
})

onMounted(refresh)       // 问出身份之后，上面那个 watch 会接手

/* 拖到页面空白处松手：浏览器不会给任何一个分类发 drop，状态得自己收干净，
   不然高亮会一直挂在那儿 */
function onPageDrop() {
  endDrag()
}
</script>

<template>
  <p v-if="!auth.ready" class="booting">正在检查登录状态…</p>

  <LoginView v-else-if="!auth.user" />

  <div v-else class="page" @dragover.prevent @drop.prevent="onPageDrop" @dragend="onPageDrop">
    <div class="groups">
      <NavGroup
        v-for="g in state.groups"
        :key="g.id"
        :group="g"
        @add="(x) => (dialog = { group: x, item: null })"
        @edit="(x) => (dialog = { group: x.group, item: x.item })"
        @remove-item="askRemoveItem"
        @remove-group="askRemoveGroup"
      />

      <button class="addgroup" @click="newGroup">
        <span class="plus">＋</span>
        <span>新建分类</span>
      </button>
    </div>

    <CardDialog
      v-if="dialog"
      :group="dialog.group"
      :item="dialog.item"
      @close="dialog = null"
    />
  </div>
</template>

<style scoped>
.booting {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  margin: 0;
  color: var(--text-3);
  font-size: 13px;
}

.page {
  padding: 36px;
}

.groups { display: flex; flex-direction: column; gap: 16px; }

.addgroup {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 52px;
  border: 1px dashed var(--border);
  border-radius: var(--radius);
  color: var(--text-3);
  font-size: 13.5px;
  transition: border-color .15s, color .15s, background .15s;
}
.addgroup:hover {
  border-color: var(--primary);
  color: var(--primary);
  background: var(--surface);
}
.plus { font-size: 20px; line-height: 1; }

@media (max-width: 600px) {
  .page { padding: 20px 16px; }
}
</style>
