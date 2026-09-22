<script setup>
import { ref, watch, onMounted } from 'vue'
import NavGroup from './components/NavGroup.vue'
import CardDialog from './components/CardDialog.vue'
import LoginView from './components/LoginView.vue'
import AccountDialog from './components/AccountDialog.vue'
import UsersDialog from './components/UsersDialog.vue'
import ImportDialog from './components/ImportDialog.vue'
import { state, removeItem, addGroup, removeGroup, boot, unbind } from './store'
import { end as endDrag } from './drag'
import { auth, refresh, logout } from './auth'

const dialog = ref(null)   // { group, item } | null，item 为 null 表示新增

/**
 * 右上角那一小块。界面本来是被刻意削到只剩分类和卡片的（见 CLAUDE.md），
 * 这里加回来的只有多用户绕不过去的那几项：**当前登录的是谁**、账号设置、
 * 用户管理（管理员）、退出。
 *
 * 一个人的门户不需要「我是谁」，多个人的必须有——不然同一台电脑上换了个人登，
 * 看着一模一样的页面，改了半天才发现改的是别人的那份。
 */
const panel = ref('')      // '' | 'account' | 'users' | 'import'
const menu = ref(false)

function closeMenu() {
  menu.value = false
}

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

/* 点页面别处收起菜单。挂在 window 上而不是给遮罩层加一个全屏 div：
   那个 div 会把底下卡片的拖拽也一起吃掉 */
onMounted(() => window.addEventListener('click', closeMenu))

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
    <div class="who" @click.stop>
      <button class="whobtn" @click="menu = !menu">
        {{ auth.user.display_name || auth.user.username }}
        <span class="caret">▾</span>
      </button>
      <div v-if="menu" class="menu">
        <button @click="panel = 'import'; menu = false">导入导航</button>
        <button @click="panel = 'account'; menu = false">账号</button>
        <button v-if="auth.user.is_admin" @click="panel = 'users'; menu = false">用户管理</button>
        <button class="out" @click="menu = false; logout()">退出登录</button>
      </div>
    </div>

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

    <ImportDialog v-if="panel === 'import'" @close="panel = ''" />
    <AccountDialog v-if="panel === 'account'" @close="panel = ''" />
    <UsersDialog v-if="panel === 'users'" @close="panel = ''" />
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

/* 右上角那一小块。绝对定位而不是在 .groups 上面塞一行：
   塞一行的话分类整体被往下推，一屏能看到的卡片就少一排 */
.who { position: absolute; top: 14px; right: 18px; z-index: 20; }
.whobtn {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 5px 10px;
  font-size: 12.5px;
  color: var(--text-2);
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
}
.whobtn:hover { background: var(--surface); border-color: var(--border); }
.caret { font-size: 9px; color: var(--text-3); }
.menu {
  position: absolute;
  top: 100%;
  right: 0;
  margin-top: 4px;
  min-width: 128px;
  padding: 4px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow-lg);
}
.menu button {
  display: block;
  width: 100%;
  padding: 7px 10px;
  text-align: left;
  font-size: 13px;
  color: var(--text);
  border-radius: 6px;
}
.menu button:hover { background: var(--surface-hover); }
.menu .out { color: var(--danger); }

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
