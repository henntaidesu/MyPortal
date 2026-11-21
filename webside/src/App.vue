<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import NavGroup from './components/NavGroup.vue'
import SharedGroup from './components/SharedGroup.vue'
import CardDialog from './components/CardDialog.vue'
import LoginView from './components/LoginView.vue'
import AccountDialog from './components/AccountDialog.vue'
import UsersDialog from './components/UsersDialog.vue'
import SsoDialog from './components/SsoDialog.vue'
import AskDialog from './components/AskDialog.vue'
import { state, removeItem, addGroup, removeGroup, boot, unbind } from './store'
import { end as endDrag } from './drag'
import { auth, refresh, logout } from './auth'
import { askConfirm, askText } from './ask'
import { colorOf } from './utils'

const dialog = ref(null)   // { group, item } | null，item 为 null 表示新增

/**
 * 右上角那一小块。界面本来是被刻意削到只剩分类和卡片的（见 CLAUDE.md），
 * 这里加回来的只有多用户绕不过去的那几项：**当前登录的是谁**、账号设置、
 * 用户管理（管理员）、退出。
 *
 * 一个人的门户不需要「我是谁」，多个人的必须有——不然同一台电脑上换了个人登，
 * 看着一模一样的页面，改了半天才发现改的是别人的那份。
 */
const panel = ref('')      // '' | 'account' | 'users' | 'sso'
const menu = ref(false)

const name = computed(() => auth.user?.display_name || auth.user?.username || '')
/* 头像取名字的第一个字。按码点切，emoji 打头时不会只切下半个代理对 */
const avatar = computed(() => ([...name.value.trim()][0] || '?').toUpperCase())
/* 配色按 username 取，不按显示名：改了显示名头像颜色不该跟着变 */
const avatarColor = computed(() => colorOf(auth.user?.username))

function closeMenu() {
  menu.value = false
}

function openPanel(p) {
  panel.value = p
  menu.value = false
}

async function askRemoveItem({ group, item }) {
  const yes = await askConfirm({
    title: '删除导航', message: `删除「${item.name}」？`, ok: '删除', danger: true
  })
  if (yes) removeItem(group.id, item.id)
}

async function askRemoveGroup(group) {
  const n = group.items.length
  const message = n
    ? `删除分类「${group.name}」？里面 ${n} 个导航会一起删掉。`
    : `删除分类「${group.name}」？`
  const yes = await askConfirm({ title: '删除分类', message, ok: '删除', danger: true })
  if (yes) removeGroup(group.id)
}

async function newGroup() {
  const name = await askText({ title: '新建分类', label: '分类名', value: '新分类', ok: '新建' })
  if (name !== null) addGroup(name)
}

/**
 * 别的系统来门户单点登录、而人还没登录时，后端把他送到 `/?continue=/api/oidc/authorize?...`
 * （app/routers/idp.py）。登进来之后照着它跳回去，授权端点会接着把人送回那个系统。
 *
 * **只认 `/api/oidc/authorize` 开头的**：不拦的话 `?continue=https://坏站` 就是一个
 * 开放重定向，地址栏上一跳还是门户，看着很可信。
 */
function continueSso() {
  const target = new URLSearchParams(location.search).get('continue') || ''
  if (!target.startsWith('/api/oidc/authorize?')) return false
  location.replace(target)
  return true
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
  if (user && continueSso()) return
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
    <div class="who" @click.stop @keydown.esc="closeMenu">
      <button class="whobtn" :class="{ open: menu }" :title="auth.user.username" @click="menu = !menu">
        <span class="avatar" :style="{ background: avatarColor }">{{ avatar }}</span>
        <span class="whoname">{{ name }}</span>
        <svg class="caret" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4" /></svg>
      </button>

      <Transition name="pop">
        <div v-if="menu" class="menu">
          <div class="mhead">
            <span class="avatar lg" :style="{ background: avatarColor }">{{ avatar }}</span>
            <div class="mwho">
              <div class="mname">{{ name }}</div>
              <div class="msub">
                <span v-if="auth.user.display_name && auth.user.display_name !== auth.user.username" class="uname">
                  @{{ auth.user.username }}
                </span>
                <span class="role" :class="{ admin: auth.user.is_admin }">
                  {{ auth.user.is_admin ? '管理员' : '普通用户' }}
                </span>
              </div>
            </div>
          </div>

          <div class="sep"></div>

          <button @click="openPanel('account')">
            <svg class="mi" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></svg>
            账号
          </button>
          <button v-if="auth.user.is_admin" @click="openPanel('users')">
            <svg class="mi" viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="8" r="3.5" /><path d="M2.5 20a6.5 6.5 0 0 1 13 0" /><path d="M16 4.5a3.5 3.5 0 0 1 0 7" /><path d="M18 14.2a6.5 6.5 0 0 1 3.5 5.8" /></svg>
            用户管理
          </button>
          <button v-if="auth.user.is_admin" @click="openPanel('sso')">
            <svg class="mi" viewBox="0 0 24 24" aria-hidden="true"><circle cx="7.5" cy="15.5" r="4.5" /><path d="m10.7 12.3 9.8-9.8" /><path d="m17 6 3 3" /><path d="m14.5 8.5 2 2" /></svg>
            单点登录
          </button>

          <div class="sep"></div>

          <button class="out" @click="menu = false; logout()">
            <svg class="mi" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><path d="m16 17 5-5-5-5" /><path d="M21 12H9" /></svg>
            退出登录
          </button>
        </div>
      </Transition>
    </div>

    <div class="groups">
      <!-- 管理员授权过来的单点登录卡片，只读，排在最前面。没有就整块不画 -->
      <SharedGroup />

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

    <AccountDialog v-if="panel === 'account'" @close="panel = ''" />
    <UsersDialog v-if="panel === 'users'" @close="panel = ''" />
    <SsoDialog v-if="panel === 'sso'" @close="panel = ''" />
  </div>

  <!-- 页面内的确认框 / 输入框（ask.js），替掉浏览器自带的 confirm() / prompt() -->
  <AskDialog />
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

/* 顶上多留的那 12px 是给右上角那颗按钮的：原来 36px 里塞不下 32px 高的按钮，
   会压在第一个分类的边框上。只多 12px，不是多一整行 */
.page {
  position: relative;
  padding: 48px 36px 36px;
}

/* 右上角那一小块。绝对定位而不是在 .groups 上面塞一行：
   塞一行的话分类整体被往下推，一屏能看到的卡片就少一排。
   right 和 .page 的左右边距一样，按钮右沿和分类大卡右沿对齐 */
.who { position: absolute; top: 8px; right: 36px; z-index: 20; }
.whobtn {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 32px;
  padding: 0 10px 0 3px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  box-shadow: var(--shadow);
  transition: background .15s, border-color .15s, box-shadow .15s;
}
.whobtn:hover { background: var(--surface-hover); }
.whobtn.open,
.whobtn:focus-visible {
  outline: none;
  border-color: var(--primary);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary) 18%, transparent);
}
.whoname {
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.caret {
  flex: none;
  width: 14px;
  height: 14px;
  fill: none;
  stroke: var(--text-3);
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
  transition: transform .18s;
}
.whobtn.open .caret { transform: rotate(180deg); }

.avatar {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
  user-select: none;
}
.avatar.lg { width: 38px; height: 38px; font-size: 16px; }

.menu {
  position: absolute;
  top: 100%;
  right: 0;
  margin-top: 8px;
  width: 232px;
  padding: 6px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-lg);
  transform-origin: top right;
}
.mhead { display: flex; align-items: center; gap: 10px; padding: 8px 8px 10px; }
.mwho { min-width: 0; }
.mname {
  font-size: 14px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.msub {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 3px;
  font-size: 12px;
  color: var(--text-3);
}
.uname { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.role {
  flex: none;
  padding: 1px 7px;
  border-radius: 20px;
  background: var(--surface-2);
  color: var(--text-2);
  font-size: 11px;
}
.role.admin {
  background: color-mix(in srgb, var(--primary) 15%, transparent);
  color: var(--primary);
}
.sep { height: 1px; margin: 4px 2px; background: var(--border); }

.menu button {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  height: 34px;
  padding: 0 10px;
  text-align: left;
  font-size: 13px;
  color: var(--text);
  border-radius: 8px;
  transition: background .12s, color .12s;
}
.menu button:hover { background: var(--surface-hover); }
.mi {
  flex: none;
  width: 16px;
  height: 16px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
  color: var(--text-3);
  transition: color .12s;
}
.menu button:hover .mi { color: var(--text); }
.menu .out { color: var(--danger); }
.menu .out .mi,
.menu .out:hover .mi { color: inherit; }
.menu .out:hover { background: color-mix(in srgb, var(--danger) 12%, transparent); }

.pop-enter-active,
.pop-leave-active { transition: opacity .14s, transform .14s; }
.pop-enter-from,
.pop-leave-to { opacity: 0; transform: translateY(-4px) scale(.97); }

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
  .page { padding: 48px 16px 20px; }
  .who { right: 16px; }
}
</style>
