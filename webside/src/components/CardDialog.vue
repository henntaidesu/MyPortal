<script setup>
import { reactive, ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import NavIcon from './NavIcon.vue'
import { state, addItem, updateItem } from '../store'
import { api } from '../api'
import { auth } from '../auth'
import { getHost, normalizeUrl, ssoLoginFrom, uid } from '../utils'

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
  // 开了就把上游站点的登录态存在服务器上（见 app/cookiejar.py），换设备也不用重登
  cookieJar: !!props.item?.cookieJar,
  // 单点登录：开了之后点卡片去 ssoLogin（那个系统的「门户登录」入口），关着就直接打开 url
  sso: !!props.item?.sso,
  ssoLogin: props.item?.ssoLogin || '',
  group: props.group.id          // 改成别的分类就等于把这张卡片搬过去
})

/* 新卡片的 id 在这儿先定下来：登记 IdP 客户端要在卡片落进 state 之前用它 */
const cardId = props.item?.id || uid()

/* ---- 门户这边的 IdP 客户端登记（app/idp.py 的 idp_clients 表），只有管理员看得到 ----
   不跟着导航存：client_secret 进了导航就进了 localStorage 和 /api/nav */
const isAdmin = computed(() => !!auth.user?.is_admin)
const idp = reactive({
  loaded: false,
  enabled: true,         // conf.json 的 idp.enabled，没开时提示一句
  issuer: '',
  secretSet: false,      // 已存过密钥：输入框留空 = 不改
  clientId: '',
  secret: '',
  uris: ''               // 一行一个
})

async function loadIdp() {
  if (!isAdmin.value) return
  try {
    const res = await api('/api/idp/cards/' + encodeURIComponent(cardId))
    idp.enabled = res.idp_enabled
    idp.issuer = res.issuer
    if (res.client) {
      idp.clientId = res.client.client_id
      idp.uris = res.client.redirect_uris.join('\n')
      idp.secretSet = res.client.secret_set
    }
  } catch {
    // 问不到就当没登记过，保存时照样能登记
  }
  idp.loaded = true
}

/* 默认 client_id 取域名第一段（freemm.makurochan.com → freemm）。不能带点，见 app/idp.py */
function defaultClientId() {
  const host = getHost(form.url).replace(/^www\./, '').split(':')[0]
  return (host.split('.')[0] || 'client').replace(/[^A-Za-z0-9_-]/g, '-').slice(0, 64)
}

function genSecret() {
  const bytes = new Uint8Array(32)
  crypto.getRandomValues(bytes)
  idp.secret = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

// 打开开关时把空着的几栏先填上：client_id 推一个、密钥现生成一串，人只需要填回调地址
function fillIdpDefaults() {
  if (!form.sso || !isAdmin.value || !idp.loaded) return
  if (!idp.clientId) idp.clientId = defaultClientId()
  if (!idp.secretSet && !idp.secret) genSecret()
}
watch(() => [form.sso, idp.loaded], fillIdpDefaults)

const uriList = computed(() => idp.uris.split(/\s+/).map((u) => u.trim()).filter(Boolean))
const derivedLogin = computed(() => ssoLoginFrom(uriList.value[0]))

const copied = ref('')
async function copy(text, key) {
  try {
    await navigator.clipboard.writeText(text)
    copied.value = key
    setTimeout(() => { if (copied.value === key) copied.value = '' }, 1200)
  } catch {
    // http 部署下没有剪贴板 API，字段本身选得中，手动复制
  }
}

const error = ref('')
const nameInput = ref(null)

/* 这张卡片在服务器上存了多少条登录数据。只有编辑已有卡片时才问得出来——
   新卡片还没有 id，罐子也就无从谈起 */
const jar = ref(null)

async function loadJar() {
  if (!props.item?.id) return
  try {
    jar.value = await api('/api/cookies/' + encodeURIComponent(props.item.id))
  } catch {
    jar.value = null          // 问不到就不显示那一行，不值得为它弹个错
  }
}

async function clearJar() {
  if (!confirm('清除已保存的登录数据？下次打开这张卡片要重新登录一次。')) return
  try {
    await api('/api/cookies/' + encodeURIComponent(props.item.id), { method: 'DELETE' })
    await loadJar()
  } catch (e) {
    error.value = '清除失败：' + (e?.message || '未知错误')
  }
}

// 先填地址没填名字时，用域名兜底
watch(() => form.url, (v) => {
  if (!form.name && v) form.name = getHost(v).replace(/^www\./, '')
})

const saving = ref(false)

async function submit() {
  if (saving.value) return
  if (!form.name.trim()) return (error.value = '请填写名称')
  if (!form.url.trim()) return (error.value = '请填写地址')

  if (form.sso) {
    // 登录入口没填就从第一条回调地址推（…/callback → …/login?base=…）
    if (!form.ssoLogin.trim() && isAdmin.value) form.ssoLogin = derivedLogin.value
    if (!form.ssoLogin.trim()) return (error.value = '请填写单点登录入口')
    if (isAdmin.value) {
      // 先登记客户端，失败就停在框里——卡片说「走单点登录」而门户不认这个系统，
      // 点开只会是一张报错页
      saving.value = true
      try {
        const res = await api('/api/idp/cards/' + encodeURIComponent(cardId), {
          method: 'PUT',
          body: { client_id: idp.clientId.trim(), name: form.name.trim(),
                  redirect_uris: uriList.value, client_secret: idp.secret.trim() }
        })
        idp.secretSet = !!res.client?.secret_set
      } catch (e) {
        return (error.value = e?.message || '登记失败')
      } finally {
        saving.value = false
      }
    }
  }

  if (isEdit.value) updateItem(props.group.id, props.item.id, form)
  else addItem(form.group, { ...form, id: cardId })
  emit('close')
}

function onKey(e) {
  if (e.key === 'Escape') emit('close')
}
onMounted(() => {
  window.addEventListener('keydown', onKey)
  nameInput.value?.focus()
  loadJar()
  loadIdp()
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

      <!-- 只有开了门户代理才有意义，所以关着的时候整栏不占位置。
           形状和上面两栏一致（标题 + 一个下拉框），不用勾选框，理由同上 -->
      <template v-if="form.proxy">
        <label class="field">
          <span class="lb">Cookie 代理</span>
          <select v-model="form.cookieJar">
            <option :value="false">关闭</option>
            <option :value="true">开启 · 登录态存在服务器</option>
          </select>
        </label>
        <p v-if="form.cookieJar" class="jar">
          <span>
            登录一次之后，换设备、换浏览器、清缓存都不用重登。
            <strong>登录凭证会加密存在门户的数据库里</strong>，
            谁能登进这个门户账号，谁就能以你的身份用那个站点。
          </span>
          <span v-if="jar && jar.available === false" class="warn">{{ jar.reason }}</span>
          <span v-else-if="jar && jar.count" class="saved">
            已保存 {{ jar.count }} 条（{{ jar.domains.join('、') }}）
            <button type="button" class="link" @click="clearJar">清除</button>
          </span>
        </p>
      </template>

      <!-- 只有整站模式用得上，所以平时不占位置。站点自己那几个域名后端内置了一份
           （app/proxy_webside/），这里填的是补充：图或接口在别的域名上、又没内置时才要填 -->
      <label v-if="form.proxy === 'site'" class="field">
        <span class="lb">额外域名<em>可选</em></span>
        <input v-model="form.proxyHosts" placeholder="逗号隔开，例如：example-cdn.com" @keyup.enter="submit" />
      </label>

      <!-- 开了之后点卡片先去那个系统的「门户登录」入口，回来就是登录状态；关着就直接打开地址。
           形状和上面几栏一样是「标题 + 下拉框」 -->
      <label class="field">
        <span class="lb">单点登录</span>
        <select v-model="form.sso">
          <option :value="false">关闭 · 直接打开网页</option>
          <option :value="true">开启 · 用门户账号登录</option>
        </select>
      </label>

      <template v-if="form.sso">
        <!-- 门户这边的客户端登记：只有管理员能做（等于交出「以任意人身份登进那个系统」的钥匙） -->
        <template v-if="isAdmin">
          <p v-if="idp.loaded && !idp.enabled" class="jar warn-box">
            门户还没开单点登录提供方（conf.json 的 <code>idp.enabled</code>），
            这里登记了也要等它打开、重启后才生效。
          </p>

          <label class="field">
            <span class="lb">回调地址<em>一行一个，逐字比对</em></span>
            <textarea v-model="idp.uris" rows="2" spellcheck="false"
              placeholder="例如：https://freemm.makurochan.com/mercariV2/src/use_web/sso/callback"></textarea>
          </label>

          <label class="field">
            <span class="lb">Client ID</span>
            <div class="row">
              <input v-model="idp.clientId" spellcheck="false" placeholder="字母、数字、_ 和 -"
                @keyup.enter="submit" />
              <button type="button" class="btn" @click="copy(idp.clientId, 'id')">
                {{ copied === 'id' ? '已复制' : '复制' }}
              </button>
            </div>
          </label>

          <label class="field">
            <span class="lb">Client Secret</span>
            <div class="row">
              <input v-model="idp.secret" spellcheck="false"
                :placeholder="idp.secretSet ? '已设置，留空不改' : '至少 32 个字符'" @keyup.enter="submit" />
              <button v-if="idp.secret" type="button" class="btn" @click="copy(idp.secret, 'secret')">
                {{ copied === 'secret' ? '已复制' : '复制' }}
              </button>
              <button type="button" class="btn" @click="genSecret">生成</button>
            </div>
          </label>
        </template>

        <label class="field">
          <span class="lb">登录入口<em v-if="isAdmin">可选</em></span>
          <input v-model="form.ssoLogin" spellcheck="false" @keyup.enter="submit"
            :placeholder="derivedLogin ? '留空：' + derivedLogin : '那个系统的「门户登录」地址'" />
        </label>

        <p v-if="isAdmin" class="jar">
          在那个系统的单点登录设置里填：门户地址
          <code class="pick" title="点一下复制" @click="copy(idp.issuer, 'iss')">{{ idp.issuer || '…' }}</code>
          <span v-if="copied === 'iss'">（已复制）</span>，
          Client ID、Client Secret 与上面一致。
          <strong>密钥保存后不再显示</strong>，忘了就点「生成」换一串，两边一起改。
        </p>
      </template>

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
        <button class="btn primary" :disabled="saving" @click="submit">{{ isEdit ? '保存' : '添加' }}</button>
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
.jar {
  margin: 8px 0 0;
  padding: 9px 11px;
  border-radius: var(--radius-sm);
  background: var(--surface-2);
  color: var(--text-3);
  font-size: 12px;
  line-height: 1.55;
}
.jar strong { color: var(--text-2); font-weight: 600; }
.jar .saved, .jar .warn { display: block; margin-top: 6px; }
.jar .warn { color: var(--danger); }
.jar .link {
  padding: 0 0 0 4px;
  color: var(--danger);
  font-size: 12px;
  text-decoration: underline;
}

.jar code {
  padding: 0 4px;
  border-radius: 4px;
  background: var(--surface);
  color: var(--text-2);
  word-break: break-all;
}
.jar .pick { cursor: copy; }
.warn-box { color: var(--danger); margin-top: 12px; }
.row { display: flex; gap: 8px; }
.row input { flex: 1; min-width: 0; }
.row .btn { flex: none; height: 38px; }

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
