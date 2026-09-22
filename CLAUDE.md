# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

（本仓库的代码注释、文档、CLI 输出均为中文，新增内容请保持一致。）

## 这是什么

一个简单的导航页：前端是导航卡片页（Vue 3 + Vite，9920），后端是一个 Python 进程
（FastAPI，9921），负责托管页面、一个账号的登录、读写导航数据、代抓站点图标、
把开了「门户代理」那些卡片的请求替浏览器转出去（内网机器，或者只有门户这台够得着的站点）。

**没有数据库。配置和数据在同一个文件里：`backend/conf.json`。**
（仓库早先有过一套 MySQL + 单点登录 + 多用户的认证中心，已整体移除，别再往回加。）

## 常用命令

```bash
# 一键启动（Windows，装依赖 + 起前后端 + 开浏览器）
start.bat

# 后端：必须在 backend/ 目录下执行，代码用的是相对包 app
cd backend
pip install -r requirements.txt   # fastapi + uvicorn + httpx，就这三个
python -m app.main                # http://localhost:9921

# 前端：必须在 webside/ 目录下执行
cd webside
npm install
npm run dev                       # http://localhost:9920
npm run build                     # 产物 webside/dist，后端会自动托管

# 打包成单文件 exe（在仓库根目录执行）
pyinstaller.bat                   # 产物 Releases\<版本>\Portal.exe
```

没有任何命令行管理工具，也不需要：要改口令或导航，直接编辑 `conf.json`。

**本仓库没有测试、没有 linter、没有 CI**。改动靠手动跑起来验证：`start.bat` 起前后端，
登录 → 加一张卡片 → 刷新看还在不在 → 确认 `backend/conf.json` 的 `nav` 段变了。

> 起服务前先确认 9921 没被上一次的进程占着。占着的话新进程会静静退出，
> 请求全被**老代码**应答，表现成「改的东西怎么不生效」。

## 架构要点

### 两个进程，两种同源方式

开发态：vite(9920) 把 `/api` 代理到后端(9921)，`changeOrigin: false`——浏览器看到的是
同源，Cookie 才能带上。部署态：`npm run build` 后由 FastAPI 直接托管 `webside/dist`，
本来就同源。**两种模式下都不需要配 CORS**，新增接口的前缀必须落在 `/api` 下，
否则 dev 下代理不到。

`StaticFiles` 在 [backend/app/main.py](backend/app/main.py) 里**挂在最后**，接口路由先匹配，
剩下的才交给静态站点。新增路由要在 `app.mount('/')` 之前 include。

### conf.json 一个文件装下配置和数据

```json
{"server": {...}, "auth": {...}, "nav": {"title": ..., "theme": ..., "groups": [...]}}
```

文件归 [config.py](backend/app/config.py) 管，导航那一段的语义在
[navstore.py](backend/app/navstore.py)，它不直接碰磁盘，走 `config.read_section('nav')`
/ `config.replace_section('nav', ...)`。

- **写是「重读整份文件 → 只换一个顶层键 → 整份原子写回」**（`.tmp` + `os.replace`）。
  重读那一步不能省：服务跑着的时候有人手改了 `auth.password`，存一次导航不该把它抹掉。
- **`server` 和 `auth` 只在启动时读一次**（模块级常量），改完要重启。`nav` 每次现读磁盘——
  拿启动时的快照当数据源的话，改完刷新页面会看到旧的。
- **conf.json 读不出来时直接 `SystemExit`，绝不重新生成一份。** 这个文件里装着人家
  全部的导航数据，坏了就停下来让人自己看，覆盖一次等于把数据删了。
  （`nav` 那一段单独坏掉是另一回事，那只当「还没配过」，页面照常打得开。）
- **生成出来的 `nav` 是 `null`，不是一份空导航。** 前端靠「nav 为 null」判断这是台还没
  配过的机器，会把本机缓存那份推上来。给成 `{"groups": []}` 的话，老用户第一次打开会
  看到一片空，而且缓存再也推不上去。
- **老版本的 `conf.ini` / `nav.json` 首次启动自动折进来**（`_migrate`），读完**不删**——
  删用户的数据不该由程序替人决定。configparser 读出来一律是字符串，所以迁移时按模板
  里的类型转回去（`_coerce`），不然生成的文件里会是 `"port": "9921"`。

### 登录：一个账号，签名 Cookie，服务端不存会话

口令明文写在 `conf.json` 的 `auth` 段，校验和会话都在 [auth.py](backend/app/auth.py)。

- **会话是一枚 `<过期时间戳>.<HMAC 签名>` 的 Cookie，服务端什么都不存。**
  没有会话表、没有内存字典，所以重启不掉线，也不存在「会话攒一堆要清」。
- **签名密钥是从口令 scrypt 出来的，不是配置项。** 这样改口令即刻让所有已发出的
  Cookie 失效——不然改完口令，拿着旧 Cookie 的人还能接着用，改口令就白改了。
  用 scrypt 而不是 sha256：口令熵低，密钥若能快速枚举，谁拿到一枚 Cookie 就能离线
  爆破出口令，而服务端那道限流管不着离线爆破。`n=16384, r=8` 要 16 MB，
  在 `hashlib.scrypt` 默认 32 MB 上限内，调大之前先确认这一点。
- **`check_credentials` 不短路**：写成 `a and b` 的话用户名错时根本不比口令，
  两条路耗时不同，能被拿来探用户名。登录失败也不区分原因。
- **登录限流是进程内内存**（`_fails` dict）。多 worker 起 uvicorn 会让它失效，
  目前设计就是单进程。
- **`COOKIE_SECURE` 反向陷阱**：https 部署必须开，但 http 环境下开了浏览器会**直接丢掉**
  登录 Cookie，表现为登录后立刻又变成未登录。
- **`require_login` 是仓库里唯一一处权限校验**，`/api/nav`、`/api/icon`、`/api/proxy`
  都挂在 `dependencies` 上（`/api/proxy` 的 WebSocket 那条是自己查 Cookie，见下面那节）。
  `/api/me` 故意**不**要求登录（没登录返回 200 + `user: null`）：
  页面一起来就要问一次，回 401 会触发前端那条「会话过期」的通路，把「本来就还没登录」
  报成掉线。
- **`/api/icon` 要求登录**，因为它会替调用方发请求。但登录之后**不拦内网地址**——
  拦了就取不回 `192.168.x.x` 那些系统的 favicon，而门户存在的意义正是指向那些地址。

### 导航数据：分类 → 卡片两层，后端权威，localStorage 只是缓存

```
{ title, theme, groups: [ { id, name, items: [ { id, name, url, desc, icon, proxy } ] } ] }
```

一个分类在页面上画成一张大卡（[NavGroup.vue](webside/src/components/NavGroup.vue)）。
前端这一侧全在 [webside/src/store.js](webside/src/store.js) 的 `pull()` / `push()` 两个
函数里，其余代码不关心数据从哪来——这是该文件刻意保持的边界。

- **老的扁平 `items` 结构要一直认**：`normalize()` 见到顶层 `items` 就收进一个默认分类，
  后端 `navstore.clean` 也有同一条分支。去掉的话，升上来的人第一次打开是一片空。
- **拖拽状态在 [drag.js](webside/src/drag.js)，不在 store.js**：那边是要存盘的数据，
  这边纯粹是鼠标此刻在哪。也没做成 props 一层层往下传——分类大卡和卡片都要读它。
  不走 `dataTransfer`：它只能传字符串，而且 dragover 时读不出来，没法一边拖一边判断高亮。
- **分类只有 `⠿` 把手可拖**，不是整个分类头：整张头都可拖的话，里面那个 contenteditable
  的分类名就没法用鼠标选字了。
- **进页面先画缓存不等接口**，后端连不上时照常能看能改。改动一发生就打一个
  `portal-nav:dirty` 标记，推成功才清掉；下次进来看到标记就**先推后拉**，
  免得把本机没同步的改动冲掉。
- **界面是被刻意削到只剩分类和卡片的**：标题栏、主题切换、搜索、退出、导入导出、
  底部统计，都是按要求逐条去掉的，不是漏写的。加回去之前先问一声。
  （卡片表单的 label 和 placeholder 一度也被去掉，后来按要求加回来了：
  编辑态字段是填好的，placeholder 顶不上来，只剩 label 认得出哪栏是哪栏。）
- **`nav.title` 页面上不显示了，但照样读进来、照样写回去**：谁在 conf.json 里手写了
  一个标题，不该被下一次存导航悄悄抹掉。`state.theme` 同理（没有切换按钮了，
  但 `applyTheme` 照常跟着它走，默认 auto = 跟随系统）。
- **页面上没有任何「存失败了」的提示**：`sync.offline` 还在 store.js 里照常维护，
  但没有 UI 读它了。要加提示的话接这个字段，别另起一套。
- **冲突是后写的盖先写的**，不做合并。自己给自己看的一页东西，为它做合并不值当。
- **卡片和分类里有哪些字段后端不管**（`navstore.clean` 只看标题、主题、条数和体积，
  别的字段原样带过）：让后端跟着校验的话，卡片上加个字段就得两头一起改。
  加字段只改 store.js 的 `normalize()`。
- **401 由 [api.js](webside/src/api.js) 的 `setUnauthorizedHandler` 统一接**，
  [auth.js](webside/src/auth.js) 注册进去把 `auth.user` 清掉退回登录页；App.vue 再
  `watch` 到它停掉自动保存，不然它会对着 401 一直重试。反向 import 会成环，所以用回调。

### 会踩的坑

- **进出登录由 `watch(() => auth.user)` 驱动，别改回「LoginView 登录成功后 emit 一下」**：
  登录成功那一刻 `auth.user` 被赋值，Vue 随即把 LoginView 卸载掉，emit 正好赶在自己
  被卸载的同一拍上，事件就丢了。表现很阴：页面确实进来了，但底部一直显示「正在同步…」，
  后端一个 `/api/nav` 请求都没发出去，改的东西全只在内存里。这个坑实际踩过。
- **门户挂在哪个路径都行**，但静态资源用的是相对路径（`vite.config.js` 的 `base: './'`），
  改这项之前先确认 `/api` 还能被反代到。
- **start.bat 只能用 ASCII**：cmd 按系统 ANSI 代码页（936）解析 .bat，
  UTF-8 中文会被逐字节错位配对，`rem` 注释的后半截会被当成命令执行。
  （`pyinstaller.bat` 里还留着中文注释，是历史遗留，别照着学。）
- **用 Python 脚本批量改文件时注意反斜杠**：写 `"backend\nav.json"` 里的 `\n` 是换行，
  搜不到也不会报错，只会静默替换 0 次。用 `r"..."` 或 `\\`，并且每次 `replace` 都
  `assert old in s`。这个坑在这份代码上踩过两次。
- 目录名是 `webside`（不是 website），别顺手改。
- `backend/conf.json` 含明文口令和全部数据，已在 .gitignore 里，仓库里**不该**出现它。
  没有 .example 模板文件，缺文件时由 config.py 的 `_DEFAULT` 现生成一份。

### 门户代理：卡片上的开关，后端 [proxy.py](backend/app/proxy.py) 转发

门户进程跑在**能打开那个站**的网络里，人在外面只连得上门户。卡片编辑框里把
「门户代理」那一栏打开之后，这张卡片的链接就从 `http://192.168.1.10:8080` 换成
`/api/proxy/<卡片 id>`，浏览器打到门户，门户转出去，响应带回来。**没有全局开关**——
卡片上那个下拉框本身就是开关，数据在 `nav` 里，改完刷新即刻生效，不用重启。
（那一栏做成下拉框不是勾选框：和「分类」一栏同一个形状，一列对齐下来每栏都是
「标题 + 一个控件」，中间不会冒出一个跟别人不一样的方块。）

**那一栏有三挡**，存进 `nav` 的就是 `false` / `true` / `"site"`：

| 卡片上选 | `proxy` | 干什么 |
| --- | --- | --- |
| 关闭 | `false` | 直接连原地址 |
| 开启 · 直接转发 | `true` | 只转卡片这一台机器，正文一个字不改。内网后台用它 |
| 开启 · 整站 | `"site"` | 一张卡片转一整族域名，还改写页面里的地址。公网站点用它 |

内网后台多半自己用相对地址，直接转发就够，也最不容易出事。公网站点不行——
メルカリ 的脚本在 `mercdn.net`、雅虎的图在 `yimg.jp`，页面里写的又都是完整地址，
不改写的话浏览器会绕过门户直连，从**用户自己的出口 IP** 打过去，站点按地区拦人时
就成了半张页面。**整站模式里 `X-Forwarded-For` 是特意不发的**，同理：发了等于
把用户的 IP 告诉上游，这个功能就白做了。

整站模式的地址里带着主机名：

    /api/proxy/<id>/__portal__/<http|https>/<主机[:端口]>/<路径>

- **能转到哪些主机由白名单说了算，这是唯一的安全边界。** 主机名是从 URL 里来的，
  谁登进来都能随手填一个，不拦的话这个代理就成了「想连哪台连哪台」的开放中继
  （SSRF 中继），而门户多半正站在内网里。白名单 = 卡片地址的注册域（子域算数）
  + 站点模块带的那几个（见下）+ 卡片上手填的「额外域名」，拼在
  [proxyrewrite.py](backend/app/proxyrewrite.py) 的 `allow_for`。
- 直接转发模式只认卡片自己那台机器，地址里塞不进主机名（老形状的链接继续认）。
- 两种模式都只转 http/https（卡片填 `ssh://` 的也有，那种转不了）。
- **要登录**，这是仓库里最该要登录的接口：它把门户的内网可达性借给了调用方。
  WebSocket 上**不能**挂 `require_login`——它抛 HTTPException，握手这会儿没人接，
  结果是 500 而不是干净的拒绝；那条路自己查一遍 Cookie，不对就按 1008 关掉。
- **转给上游之前要把 `portal_session` 从 Cookie 里摘掉。** 上游页面在浏览器眼里
  和门户同源，所以这枚 Cookie 会跟着发过来；原样转过去等于把登录门户那张票
  交给内网那台机器。上游自己种的 Cookie 不受影响：它们的 `Path` 被改写成
  `/api/proxy/<id>/`，只跟着自己这个服务走（不改的话几个系统的同名 Cookie 会互相顶掉，
  表现成「开了 B 系统 A 系统就掉登录」）。http 门户下还要把 `Secure` 去掉、
  `SameSite=None` 降成 `Lax`，不然浏览器直接不存。
- **根绝对地址（`/static/x.js`）靠 Referer 兜底**，见 proxy.py 末尾的 `Fallback`：
  Referer 说这次请求出自 `/api/proxy/<id>/…` 的页面，就 307 转回代理底下。
  连 `/api/` 开头的也兜（只放过 `/api/proxy/` 自己）——上游系统自己也会有 `/api/xxx`，
  不能被门户那几个接口截胡。**307 不能改成 308**：同一个 `/static/x.js` 好几个系统都有，
  308 会被浏览器永久记住，串到另一个系统上去。
- **`Fallback` 是纯 ASGI 中间件，别改回 `@app.middleware('http')`**：后者是
  BaseHTTPMiddleware，会把响应体整个搬进队列再吐，而这个代理专门要转大文件下载和
  SSE 长连接，经它一道就不流式了。
- **页面里写死的完整地址在整站模式下会被改写**，分两道：
  [proxyrewrite.py](backend/app/proxyrewrite.py) 改正文里的字面量（HTML/CSS/JS/JSON
  都过一遍，`https:\/\/host` 这种转义写法也认），
  [proxyhook.py](backend/app/proxyhook.py) 往页面 `<head>` 里注一段脚本，
  运行时把 `fetch` / `XHR` / `WebSocket` / 元素的 `src`、`href` 全包一层。
  直接转发模式一个字都不改（老行为原样留着）。
- **JS 里 `"/"` 开头的字符串故意不改**：那大半是路由名、正则、模板，不是地址。
  改错了页面还能跑，但行为悄悄变了，最难查。那一类交给注入的脚本在真要发请求时再判，
  再兜不住就落到 `Fallback` 那条 Referer 兜底上。
- **改写过的正文必须把 `integrity` 拆掉**：SRI 的哈希是按原文算的，改过就对不上，
  浏览器会直接拒绝执行那个脚本，表现成整页白屏。
- **`Referer` 和 `Origin` 要换成上游的真实地址**（两种模式都换）。上游拿它们做防盗链
  和跨站校验，给一条门户的地址等于说「这是从一个不认识的站点点过来的」，
  登录、下单那几步会被拒。换不回真实地址的就整条丢掉，别把门户的地址漏出去。
- **响应里那几个讲源站规矩的首部要摘掉**（`_STRIP`）。最要命的是
  `Strict-Transport-Security`：浏览器会把**门户**这个域名记成「只许 https」，
  一记一年，http 部署的门户从此打不开，还得进浏览器设置删 HSTS 才清得掉。
  `Content-Security-Policy` 是照着上游那个域名写的，搬到门户域名下只会拦到自己。
- **`__Host-` / `__Secure-` 开头的 Cookie 要改名再发给浏览器**，回传时改回去。
  这两种前缀要求 `Path=/` 和 `Secure`，而代理底下每张卡片的 Cookie 都钉在
  `/api/proxy/<id>/`，条件对不上浏览器会**直接丢掉**，表现成「登录页转一圈又回到登录页」。
- **Service Worker 在注入的脚本里被掐掉了**：它注册下来会横在所有请求前面按自己那套
  改地址，和这里两道改写打架，而且装上之后要进浏览器设置才卸得掉。
- **`verify=False`**：内网系统（PVE、群晖、带外管理口）基本都是自签证书，按标准校验
  必然失败，而失败的表现是「这张卡片点了就报 502」，谁都想不到是证书。
- **`read=None`（不设读超时）**：实时日志、SSE、长轮询都是一个请求挂很久。连接超时留着。
- **`follow_redirects=False`**：跳转要交回浏览器，由它带着 Cookie 再来一趟，
  地址栏才跟得上。`Location` 指向同一台机器时折回代理底下，指向别处的**原样留着**——
  改写的话上游回一个 `Location: 任意地址` 门户就替人去访问了，又成了开放中继。
- **`transfer-encoding` 必须滤掉**（逐跳首部）：httpx 交回来的已经是拆好块的字节，
  把这个头带回去浏览器会照着再拆一遍，拿到一堆长度前缀。
- **响应头走 `resp.headers.raw` 不走 `.items()`**：`Set-Cookie` 一次可能好几条，
  字典形状只留得下最后一条。
- **dev 下 vite 的 `/api` 代理要 `ws: true`**，否则带 web 终端、实时日志的页面
  只在 dev 卡在「连接中」，部署态是同源又好的，查起来会以为是后端的问题。
- `websockets` 是 uvicorn[standard] 顺带装的，不在 requirements 里写明，所以 import
  写在函数里（和 tray.py 一个路数）；它 13 之前叫 `extra_headers`，14 之后叫
  `additional_headers`，两个名字都认。

#### 一个站一个文件：[proxy_webside/](backend/app/proxy_webside/)

通用那两道对谁都一样，但具体到某个站总有几条只属于它的事（资源在哪几个域名上、
要不要按日语要页面、Next.js 那种把当前路径塞进首部的要还原）。这些堆进 proxy.py 会
很快变成一坨 if-else，所以**一个站一个 py 文件**，`__init__.py` 只负责按卡片地址的
主机名把模块找出来（后缀最长的赢，`auctions.yahoo.co.jp` 和
`paypayfleamarket.yahoo.co.jp` 是两个站，别互相认领）。

现在有 `mercari.py`、`yahoo_auctions.py`、`paypayfleamarket.py`，
两个雅虎站共用的域名清单在 `_yahoo.py`（下划线开头 = 不是站点模块，不进 `_MODULES`）。
模块里 `NAME` / `DOMAINS` / `HOSTS` / `BROWSER_JS` / `on_request` / `on_response` /
`on_html` 都是可选的，缺了就当没有；钩子抛异常只打一行日志，不会把请求带崩——
这些文件记的是「某个站现在这么写」，改版之后随时可能对不上。

- **被认领的卡片自动按整站办**，哪怕卡片上选的是「直接转发」：这里躺着的本来就是
  直接转发必坏的那几个站，认出来了还按直接转发办，只会让人对着半张页面查半天。
- **`_MODULES` 和 [portal.spec](portal.spec) 的 `hiddenimports` 都是写死的清单，
  新加站点文件两处都要加。** 前者别改成 pkgutil 扫目录、后者别改成
  `collect_submodules`：PyInstaller 靠静态分析决定打包什么，动态导入它看不见，
  表现成「源码跑得好好的，exe 里那个站只剩通用规则」，而且不报错。

#### 整站模式够不着的地方（知道就行，别指望）

- **`location.href = '...'` 这类赋值拦不住**：`Location` 的属性是 unforgeable，
  脚本改不了。字面量改写能覆盖大半，再兜不住就落到 `Fallback` 上。
- **门户最好挂在 https 下**。站点里 `if (location.protocol !== 'https:')` 然后自己跳转的
  写法不少，http 门户下会跳到一个不存在的地方。（`cookie_secure` 同理。）
- **按域名发牌的第三方过不了**（reCAPTCHA 那类）：域名对不上，代理与否都一样。
- **Worker 里没有注入的那段脚本**，正文改写只覆盖它里面的完整地址字面量。
- **单标签主机名（`http://nas/`）的完整地址改不着**：正文改写要求主机名里带点，
  不然 JS 源码里的 `// 这是注释` 会被当成协议相对地址改坏。根绝对和相对地址不受影响。
- **被代理的页面和门户同源**，它里面的脚本能拿门户的会话去调 `/api/nav`。
  `Fallback` 顺手挡了一道（带 Referer 的 `/api/xxx` 会被转回代理底下），但绕得开。
  内网后台是自己人，公网站点可就不是了——真要紧的站别和门户放在一起。

### 图标缓存是磁盘目录，不进 conf.json

`/api/icon` 抓回来的图落在 `ICON_DIR`（[config.py](backend/app/config.py)）里，
源码态是 `backend/icons`，打包后是 exe 同级的 `icons`。目录启动时自动建
（[iconcache.py](backend/app/iconcache.py) 的 `ensure`），**整个删掉也没事**，
下次访问自己会重建重抓；已在 .gitignore 里。

- **不塞进 conf.json**：是一堆几十 KB 的二进制，而且随时可以重抓，塞进去只会让
  「拷走一个文件就是备份」变成拷走几 MB。
- **「抓不到」也要存**（`<指纹>.miss`，6 小时）。不存的话，一个没有 favicon 的站点
  会让每次开门户都去外网白跑一趟，超时还得干等 6 秒。抓到的存 7 天。
- **写缓存先写 `.tmp` 再 `os.replace`**：中途断电不会留下半张图被当成好的发出去。
  漏下的 `.tmp` 残骸由每小时那趟 `purge` 收走。
- **前端不自己缓存图标**。[icons.js](webside/src/icons.js) 只负责拼 `/api/icon` 的地址，
  直接交给 `<img src>`，重复访问靠响应上的 `cache-control` 让浏览器缓一天。

## 打包成 exe

`pyinstaller.bat` + [portal.spec](portal.spec)，产物是单文件 `Releases\<版本>\Portal.exe`，
发布目录里**只有这一个文件**。入口是 [backend/main.py](backend/main.py)，**没有子命令**：
双击就是起服务。

图标三处同一张：exe 图标、托盘图标、运行窗口图标都用门户网页的 favicon
（`webside/public/favicon.ico` / `favicon.png`，指南针）。换图标只换这两个文件，
spec 和 [tray.py](backend/app/tray.py) 的 `icon_path` 会跟着走。

### 双击是桌面程序

exe 打成 **windowed（`console=False`）**：双击不弹 CMD 黑框，起来的是一个运行窗口
（[logwindow.py](backend/app/logwindow.py)）实时显示 uvicorn 日志，点 X 问「收进托盘 /
退出程序」，收进托盘后服务继续在后台跑，右下角托盘图标（[tray.py](backend/app/tray.py)）
能再打开窗口、打开门户、优雅退出。

这几条是踩出来的，改之前先读：

- **别改回 `console=True` + `hide_console`。** PyInstaller 那个 `hide_console='hide-early'`
  靠 `ShowWindow(GetConsoleWindow())` 藏窗口，而 Win11 默认终端是 Windows Terminal，
  拿到的是个代理窗口——实测黑框从启动到退出全程挂着，等于没做。
- **windowed 的代价：进程启动时没有控制台，`sys.stdout/stderr` 全是 None。**
  所以 `logwindow` 的 `_Tee` 必须能接受 `base=None`，而且得是个合法文件对象：
  uvicorn 配日志时会问 `isatty()`，logging 会调 `write`/`flush`，哪个抛异常都能把服务带崩。
- **启动失败时要把窗口按住**（`logwindow.hold`）。`SystemExit('conf.json 读不出来')` 那句
  提示是解释器退出时才打的，那会儿窗口早跟着进程没了——没有控制台可以退，双击的人
  只会看到「闪一下，什么都没有」。
- **tkinter / pystray / Pillow 不在 `backend/requirements.txt` 里**，只有 `pyinstaller.bat`
  打包前会装。源码态起服务不该为了一个托盘图标去装 pystray，所以这三个模块里的
  import 全写在函数里，缺了就静默跳过。spec 的 `excludes` 里**不能**再排 `tkinter` 和 `PIL`。

冻结后路径规则全在 [config.py](backend/app/config.py) 的 `FROZEN` 分支里：

- **`conf.json` 绝不能打进 exe。** 打进去的话每次启动都被临时解压目录里的那份盖掉，
  改了口令、加了卡片全白改；而且登录口令会跟着 exe 一起发出去。它同时是用户的全部
  数据，更不能进。所以 `BASE_DIR` 在冻结态取的是 `sys.executable` 所在目录，
  不是 `sys._MEIPASS`。
- **`ICON_DIR` 同理，跟着 `BASE_DIR` 走**（exe 同级的 `icons`），别顺手改成 `_MEIPASS` 下面——
  那是每次启动现解压的临时目录，缓存写进去等于没缓存。
  所以 exe 跑起来之后发布目录里会多出 `conf.json` 和 `icons/`，这是正常的。
- **前端打进 exe**（解压在 `_MEIPASS/webside`），但 exe 同级放一个 `webside` 目录就能盖掉它，
  换前端不用重新打包。
- **spec 里用 `SPECPATH` 定位项目根，不要用 `os.getcwd()`**：从别的目录调起来会算错，
  表现成「前端悄悄没打进 exe」。dist 不存在时 spec 直接 `SystemExit`，不留只警告的余地——
  少了前端的 exe 照样能跑，只是首页一片空，到现场才发现就晚了。
- **spec 里不用 `collect_all`。** 它会把 `fastapi.testclient`、`pydantic.mypy`、
  `anyio.pytest_plugin` 这类可选子模块一并列成隐藏导入，在 anaconda base 这种什么都装了的
  环境里会顺藤摸瓜拖出 pytest / IPython / Qt，最后以「multiple Qt bindings」构建失败。
  依赖全是纯 Python，靠静态分析加自带 hook 就够，只有 uvicorn 那几个按字符串名字导入的
  协议实现要手写进 `hiddenimports`。
- 脚本自己就叫 `pyinstaller.bat`，所以里面必须写 `python -m PyInstaller`——
  裸写 `pyinstaller` 会被 cmd 解析成这个脚本本身，死循环。
- **发布目录只放 Portal.exe**：`conf.json` 不从本机拷，那里面是这台机器自己的口令和数据。
