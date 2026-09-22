# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

（本仓库的代码注释、文档、CLI 输出均为中文，新增内容请保持一致。）

## 这是什么

一个多用户导航页：前端是导航卡片页（Vue 3 + Vite，9920），后端是一个 Python 进程
（FastAPI，9921），负责托管页面、登录（本地账号 + OIDC 单点登录）、按人读写导航数据、
代抓站点图标、把开了「门户代理」那些卡片的请求替浏览器转出去（内网机器，
或者只有门户这台够得着的站点）。

**数据在 MySQL 里，配置在 `backend/conf.json` 里，两者不混。**
库里五张表：`users`（账号和口令哈希）、`user_identities`（OIDC 绑定）、
`nav_prefs` / `nav_groups` / `nav_items`（每人一份的导航），外加一张
`portal_meta` 放签名密钥和迁移标记。conf.json 只剩连接串、监听地址、OIDC 参数。

**每个人看到的是自己那份导航**，互相看不见，卡片 id 也不是全局的
（两个人撞同一个 id 是允许的）。卡片上还能开 **Cookie 代理**——把外部站点的
登录态加密存在服务器上，换设备、清缓存都不用重登（见门户代理那一节）。

## 常用命令

**先得有一个连得上的 MySQL**（5.7+ / 8.x，或 MariaDB 10.2+），库和表由程序自己建，
但账号得有 `CREATE DATABASE` 和建表的权限。连不上就 `SystemExit`，服务根本起不来——
这一版没有「退回单机模式」的余地。

```bash
# 一键启动（Windows，装依赖 + 起前后端 + 开浏览器）
start.bat

# 后端：必须在 backend/ 目录下执行，代码用的是相对包 app
cd backend
pip install -r requirements.txt   # fastapi + uvicorn + httpx + PyMySQL + cryptography
python -m app.main                # http://localhost:9921

# 前端：必须在 webside/ 目录下执行
cd webside
npm install
npm run dev                       # http://localhost:9920
npm run build                     # 产物 webside/dist，后端会自动托管

# 打包成单文件 exe（在仓库根目录执行）
pyinstaller.bat                   # 产物 Releases\<版本>\Portal.exe
```

没有命令行管理工具。**改口令、加用户、改导航都在页面上做**（右上角那个菜单）：
口令是哈希存的，改 conf.json 已经改不动它了。唯一还要编辑 conf.json 的是数据库连接、
监听地址和 OIDC 参数，改完要重启。

第一个管理员由 `auth.bootstrap_admin` 建，**只在 users 表为空时看这一段**——
已经有人了就完全不管它，所以配置里留着那对用户名口令既不会重复建号，
也不会把人家在页面上改过的口令改回去。

**本仓库没有测试、没有 linter、没有 CI**。改动靠手动跑起来验证：`start.bat` 起前后端，
登录 → 加一张卡片 → 刷新看还在不在 → 到库里 `SELECT * FROM nav_items` 看一眼。
多用户那部分再多验一步：**另开一个隐身窗口用第二个账号登进去，确认看到的是空的**。

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

### conf.json 只剩配置，数据在 MySQL

```json
{"server": {...}, "database": {...}, "auth": {...}, "oidc": {...}}
```

文件归 [config.py](backend/app/config.py) 管，**整份只在启动时读一次**，改完要重启。
这一版没有「跑着的时候改 conf.json」这回事了：会变的东西（口令、用户、导航）都在库里。

- **conf.json 读不出来时直接 `SystemExit`，绝不重新生成一份。** 里面装着数据库口令和
  OIDC 的 client_secret，覆盖一次就得全部重配。
- **`auth.bootstrap_admin` 只在 users 表为空时看**，见上面那节。
  原地升级上来的 conf.json 里只有单账号时代那对 `auth.username` / `auth.password`，
  **config.py 里那条退回去认它们的分支不能删**——不认的话库里一个用户都建不出来，
  表现成「升完级谁都登不进去」，而人手里那份 conf.json 看着完全正常。
- **老版本的 `conf.ini` / `nav.json` 首次启动仍然自动折进来**（`_migrate`），
  `nav` 那一段接着会被搬进数据库（[navstore.py](backend/app/navstore.py) 的
  `import_legacy`，搬进第一个管理员名下，搬没搬过记在 `portal_meta` 里）。
  **读完都不删**——删用户的数据不该由程序替人决定。
- **两条登录路都关了（`allow_local_login` false 且 `oidc.enabled` false）就直接停下来**：
  那等于谁都进不来，而登录页上一个按钮都不会有，只会是一片空白。

### 数据库：五张表，PyMySQL 直连，自己写 SQL

（加上 Cookie 代理那张 `proxy_cookies` 其实是六张。）
表结构在 [db.py](backend/app/db.py) 的 `_DDL` 里，全是 `CREATE TABLE IF NOT EXISTS`，
每次启动跑一遍。库不存在会先 `CREATE DATABASE`，所以部署时不用手工建库。

- **不用 SQLAlchemy。** 表就五张，查询都是「按 user_id 取一棵两层树」，ORM 省不下什么；
  而 **PyInstaller 靠静态分析决定打包什么**，SQLAlchemy 的方言和池实现全是按字符串
  动态导入的，漏一个的表现是「源码跑得好好的，exe 一连库就 ModuleNotFoundError」。
  PyMySQL 是纯 Python 单包，import 全是静态的。
- **连接池是 `LifoQueue`，取出来先 `ping(reconnect=True)`。** FastAPI 的同步路由跑在
  anyio 的线程池里（默认 40 条线程），一条全局连接会被两个请求同时用，把协议帧串掉，
  表现成莫名其妙的 `Packet sequence number wrong`。ping 那一下是为了 `wait_timeout`——
  门户半夜没人用，早上第一个请求撞上「MySQL server has gone away」几乎是必然的。
- **`cursor()` 默认只读，走完 rollback。** InnoDB 在第一条 SELECT 时就开了事务，
  不关的话这条连接一直挂着当时那个快照，被它服务的请求读到的数据会越来越旧。
- **字符集必须 utf8mb4。** 三字节的 utf8 存不下 emoji，而卡片名字里很容易出现一个，
  表现成插入时 `Incorrect string value`。
- **`desc` 是 MySQL 保留字**，卡片描述那一列叫 `descr`。
- **签名密钥在 `portal_meta` 里，不是配置项。** 让人往 conf.json 里填一串随机数多半
  会被填成 `secret`；而多实例部署时两边填得不一样，表现成「刷新一下就掉线」。

### 登录：多用户，本地口令 + OIDC，签名 Cookie，服务端不存会话

口令哈希在 `users` 表里（[users.py](backend/app/users.py)），会话在
[auth.py](backend/app/auth.py)，单点登录在 [oidc.py](backend/app/oidc.py)。

- **会话仍然是一枚 Cookie，服务端什么都不存**，形状是
  `<用户 id>.<token_version>.<过期时间戳>.<HMAC 签名>`。没有会话表、没有内存字典，
  所以重启不掉线。
- **`token_version` 替掉了上一版「密钥从口令 scrypt 出来」那个把戏。** 上一版只有一个
  账号，改口令换密钥就能让所有 Cookie 失效；多用户之后一把密钥对不上 N 个口令，
  所以改口令 / 停用 / 踢下线时给**那个人**的 `token_version` +1，只作废他一个人的票。
  **改口令必须 +1**：不 +1 的话口令泄露后改口令就白做了，对方手里那枚 Cookie 还能用到过期。
- **口令是 scrypt，每人一把随机盐，参数写在串里**（`scrypt$n$r$p$盐$结果`）。
  参数写死在代码里的话，调大 n 那天所有人都得重置口令。`n=16384, r=8` 要 16 MB，
  在 `hashlib.scrypt` 默认 32 MB 上限内，调大之前先确认这一点。
- **`check_password` 不短路，用户不存在时也照算一遍 scrypt**：不算的话「这个用户名
  存不存在」「这个人有没有本地口令」都能从响应时间上看出来。登录失败也不区分原因。
- **纯 OIDC 用户的 `password_hash` 是 NULL，不是空串。** 空串会走进「比一比口令」
  那条路；NULL 直接返回 False，本地登录这条路对他是关着的。
- **`require_login` 返回的是用户那一行，按用户取数据的地方一律从它拿 `user_id`**，
  别从请求体里读——读请求体等于让调用方自己说他是谁。管理员接口挂 `require_admin`，
  挂在**路由上**不是函数体里，以后新增一条路也漏不掉。
- **`auth` 里那层 10 秒的用户缓存是权限撤销的生效上限，别调长。** 它是为了让打开一个
  被代理的页面（一口气几十个子资源请求）不至于把连接池占满。同进程里改完会被
  `forget()` 当场清掉，所以只有多实例部署才看得到这个延迟。
- **`/api/me` 故意不要求登录**（没登录返回 200 + `user: null`）：页面一起来就要问一次，
  回 401 会触发前端那条「会话过期」的通路，把「本来就还没登录」报成掉线。
  `/api/auth/providers` 同理——正是没登录的人才需要它。
- **`COOKIE_SECURE` 反向陷阱**：https 部署必须开，但 http 环境下开了浏览器会**直接丢掉**
  登录 Cookie，表现为登录后立刻又变成未登录。
- **登录限流是进程内内存**（`_fails` dict）。多 worker 起 uvicorn 会让它失效，
  目前设计就是单进程。

#### OIDC 这一路

- **不引 authlib / python-jose**：只用得上授权码流程这一条路，而那两个包带进来的是
  一整套 JOSE 实现和 `cryptography` 这个二进制扩展。门户的依赖全是纯 Python，
  加一个带 C 扩展、还按字符串名字动态选算法后端的包，换来的是只在打包后才出现的问题。
- **id_token 的签名不验，靠 TLS。** OIDC Core 3.1.3.7 第 6 条写明了：token 是客户端
  带着 client_secret 直接从 token 端点、经 TLS 取回来的时候可以不验签——中间没有
  第三方经手。**但 `iss` / `aud` / `exp` / `nonce` 四项一条都不能少**，它们防的是别的东西
  （指到假 IdP、拿别的客户端的 token 来换会话、重放）。
- **认人只认 `sub`，不认 email 也不认用户名。** 那两个在 IdP 里都能改，跟着它们走会让
  改过名的人下次登录变成另一个账号，导航跟着丢。用户名只在**第一次**建号时用一下。
- **state / nonce / PKCE 存在一枚签名过的临时 Cookie 里**（`portal_oidc`，10 分钟），
  服务端什么都不存。它的 `SameSite` **必须是 lax 不能是 strict**：从 IdP 跳回来那一下
  是跨站发起的顶层导航，strict 会让浏览器不带这枚 Cookie，表现成「每次回来都说状态丢了」。
- **`next` 参数只认站内的根绝对路径**（`_safe_next`）。不拦的话这就是一个开放重定向，
  而地址栏上一跳还是门户，看着很可信。`//坏站` 也要挡——那是协议相对地址。
- **出错时是 303 回 `/?oidc_error=...`，不是回 JSON**：这条路是浏览器的顶层跳转，
  回一段 JSON 的话用户看到的是满屏大括号，而他只是点了个「单点登录」。
- **`redirect_url` 优先用配置里写死的那个**。按请求 Host 现拼出来的地址在反代后面
  多半和 IdP 那边登记的不一样，而地址对不上 IdP 会直接拒绝授权请求。

### 导航数据：每人一份，分类 → 卡片两层，后端权威，localStorage 只是缓存

```
{ title, theme, groups: [ { id, name, items: [ { id, name, url, desc, icon, proxy } ] } ] }
```

**前端看到的形状一个字没变**，变的是它落在三张表里，而且每一行都带 `user_id`。
`load` / `save` 都必须给出是谁的——没有「当前用户」这种全局状态，漏传一个参数
就变成串号。前端那个 `id` 存进 `client_id`，**不换成数据库主键**：前端整棵树拿它做 key，
拖拽、编辑、`/api/proxy/<id>` 全指着它。

**存一次 = 把这个人那棵树整个删掉再插一遍**，一个事务里做完。不做逐条 diff：
一棵树撑死几百行，diff 省下的那点写入量换不回它带来的一堆边界情况。

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
- **界面仍然是被刻意削到只剩分类和卡片的**：标题栏、主题切换、搜索、导入导出、
  底部统计，都是按要求逐条去掉的，不是漏写的。加回去之前先问一声。
  **右上角那一小块是多用户绕不过去才加回来的**（当前登录的是谁、账号、用户管理、退出）：
  一个人的门户不需要「我是谁」，多个人的必须有，不然同一台电脑上换了个人登，
  看着一模一样的页面，改了半天才发现改的是别人那份。它是绝对定位的，
  不占 `.groups` 上面一行——占一行的话一屏能看到的卡片就少一排。
  （卡片表单的 label 和 placeholder 一度也被去掉，后来按要求加回来了：
  编辑态字段是填好的，placeholder 顶不上来，只剩 label 认得出哪栏是哪栏。）
- **`nav.title` 页面上不显示了，但照样读进来、照样写回去**：谁在 conf.json 里手写了
  一个标题，不该被下一次存导航悄悄抹掉。`state.theme` 同理（没有切换按钮了，
  但 `applyTheme` 照常跟着它走，默认 auto = 跟随系统）。
- **页面上没有任何「存失败了」的提示**：`sync.offline` 还在 store.js 里照常维护，
  但没有 UI 读它了。要加提示的话接这个字段，别另起一套。
- **冲突是后写的盖先写的**，不做合并。自己给自己看的一页东西，为它做合并不值当。
- **卡片和分类里有哪些字段后端不管**（`navstore.clean` 只看标题、主题、条数和体积）。
  摊不进列的字段**原样进 `extra` 这个 JSON 列**，读出来再摊回卡片上——这是上一版
  「加字段只改 store.js」那条约定的延续。列写死的话，前端加一个字段就得改表结构、
  改 SQL、改两头的代码。
- **本机缓存的键按账号分**（`portal-nav:u<用户 id>`，见 store.js 的 `navKey()`）。
  继续用固定键的话，同一台电脑上换个账号登进来，先画出来的是**上一个人的导航**，
  而且页面一起来就把它当成「本机这份」推给后端，等于拿 A 的导航盖掉 B 的。
  单账号那一版留下的 `portal-nav` 由 `readLocal` 认领一次就搬走，第二个人登进来时
  那儿已经什么都没有了。
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
- `backend/conf.json` 含数据库口令和 OIDC 的 client_secret，已在 .gitignore 里，
  仓库里**不该**出现它。没有 .example 模板文件，缺文件时由 config.py 的 `_DEFAULT`
  现生成一份（database 那一段要人自己填）。
- **新增接口如果按用户取数据，user_id 一律从 `require_login` 拿**，别加一个
  `?user_id=` 参数，也别从请求体里读。这是这一版最容易一次性做错、而且不会报错的地方。

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
- **要登录，而且要是「这张卡片的主人」。** 归属校验就是 `_target(user_id, item_id)`
  那一句：查的是 `_targets(user_id)` 这张**按人建的**表，不是先查全局表再比对 user_id。
  比对写法只要哪天多一条取卡片的路就漏一次，而漏一次的后果是「拿到别人的卡片 id
  就能借门户往那台机器上打」。多用户之后卡片 id 不再全局唯一（前端那个 `uid()`
  是本机随机生成的），撞上就是串号。
  返回 404 时**不区分**「不是你的」「存在但没开代理」「压根没这张卡片」——分开说
  等于让人拿一串 id 去探别人的导航里有什么。
- 目标表按用户缓存，失效有两道：`navstore.revision()`（进程内计数，**同进程里存完
  导航下一个请求就看得到新的**）+ 5 秒 TTL（兜住别的进程改了库）。只留 TTL 的话，
  自己刚改完卡片地址、点进去还是老的，查起来很懵。
- WebSocket 上**不能**挂 `require_login`——它抛 HTTPException，握手这会儿没人接，
  结果是 500 而不是干净的拒绝；那条路自己查一遍 Cookie（`user_from_token`），
  不对就按 1008 关掉，查出来的 user 一样要拿去建目标表。
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

#### Cookie 代理：上游的登录态存在服务器上

卡片上另一个开关（`cookieJar`，存在 `nav_items.extra` 里，和 `proxyHosts` 一样）。
开了之后走这张卡片的请求就有一个**服务器端的 Cookie 罐子**
（[cookiejar.py](backend/app/cookiejar.py)，`proxy_cookies` 表）：

    上游 Set-Cookie ──> 存进罐子（加密）──> 也照常改写后交给浏览器
    发给上游的请求  <── 从罐子里按 RFC 6265 挑出该带的那几枚

解决的是浏览器那份靠不住：清一次数据全没、换台设备要重登、Safari 那类还会把
脚本种的 Cookie 压到 7 天。**这是唯一一个让「换设备不掉登录」成立的地方。**

- **一张卡片一个罐子，钉在 `(user_id, item_id)` 上。** 两张卡片指同一个站算两个罐子——
  按域名合并的话「我的号」和「公司的号」会互相顶掉，而那正是有人开两张卡片的理由。
- **`item_id` 存的是前端那个 client_id，不能挂外键指向 `nav_items.id`**：
  存一次导航是「整棵树删掉重插」，自增主键每次都变，挂外键等于每存一次导航
  就把人家的登录态清空一次。代价是要自己收尾，见下一条。
- **卡片删了、或者开关关了，罐子要跟着收掉**（`cookiejar.retain`，在存导航那个
  事务里调）。不收的话：删了卡片登录数据还躺在库里；关掉开关又打开还是登录态，
  那个开关就成了摆设。
- **值一律 AES-GCM 加密**（[secretbox.py](backend/app/secretbox.py)），密钥从
  `portal_meta` 那把签名密钥 HKDF 派生，不是配置项。AAD 是
  `<用户>:<卡片>:<Cookie 名>`，所以改库的人也没法把 A 的那行抄到 B 名下。
  **解不开返回 None，不报错**——最坏就是让人重登一次。
- **加密挡不住门户进程自己**：它必须解得开才能发给上游。所以这个功能真正的
  安全边界是「谁能登进这个门户账号，谁就能以你的身份用那些外部站点」。
- **唯一索引用的是 `scope_hash`（域+路径+名字的 SHA-256），不是三列直接进索引**：
  utf8mb4 下 255×3 个字符要 3060 字节，加上前两列就超过 InnoDB 那 3072 字节的上限。
- **请求侧是「罐子优先，浏览器那份补漏」**（`_cookie_out`）。两份都要：罐子那份是
  上游最近真发下来的，浏览器那份可能是陈货；但脚本用 `document.cookie` 自己种的
  东西（语言、时区、分桶）从不经过 `Set-Cookie`，罐子里没有，丢掉页面会每次重问。
- **浏览器一条 Cookie 都没带时那个分支根本不会走到**，而那恰恰是这个功能最该出场的
  时候（第一次进、刚清过数据）。所以另有一段 `if target.jar and not seen_cookie`
  把罐子那份补上去——HTTP 和 WebSocket 两条路都要，漏了 WS 的表现是
  「页面打得开，实时那块一直连接中」。
- **存罐子挂在 `resp` 刚到手那一句**，不是挂进 `_response_headers`：后者在
  「改写后整份发」和「流式转」两条出路上各调一次，挂进去会存两遍。
- **读 `Set-Cookie` 必须走 `resp.headers.raw`**：一次响应好几条是常事，字典形状只留
  得下最后一条，表现成「明明登录成功了，下次进来还是没登录」——真正管用的那枚
  正好不是最后一条。
- **`Domain` 必须是请求主机的后缀，单标签的域（`Domain=com`）一律拒。** 没有公共
  后缀表可查，这两条是仅有的防线：不拦的话代理底下任一上游都能给同族别的站种 Cookie。
- **上游用「过期时间在过去」来删 Cookie，要翻译成一条 DELETE。** 不翻译的话退出登录
  之后罐子里还留着旧会话，下次进来带着一枚作废的票，有些站点会卡在半登录状态。
- **cryptography 没装时整个功能自己关掉**（`secretbox.available()`），门户照常跑。
  明文落库比不做这个功能更糟，所以没有「先明文存着」这条退路。
- 会话 Cookie（没有过期时间的）在罐子里放 90 天，从最后一次被上游更新算起。
  跟着关页面清掉就失去意义了，但也不能永远留着。

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
  **多用户之后这条更要紧**：绕过去拿到的是**当前这个登录用户**的会话，
  他能调 `/api/nav` 改自己的导航；要是他还是管理员，`/api/users` 那一整套也在同源之内。

### 图标缓存是磁盘目录，不进数据库

`/api/icon` 抓回来的图落在 `ICON_DIR`（[config.py](backend/app/config.py)）里，
源码态是 `backend/icons`，打包后是 exe 同级的 `icons`。目录启动时自动建
（[iconcache.py](backend/app/iconcache.py) 的 `ensure`），**整个删掉也没事**，
下次访问自己会重建重抓；已在 .gitignore 里。

- **不塞进数据库**：是一堆几十 KB 的二进制，而且随时可以重抓。塞进去只会让备份
  和主从同步平白多扛几 MB，换不回任何东西——整个目录删掉，下次访问自己会重建重抓。
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

**这一版的 exe 不再是「拷过去双击就能用」**：它要连一个 MySQL。发布目录里还是
只有 `Portal.exe` 一个文件，但第一次跑起来要先把它生成的 `conf.json` 里 database
那一段填对，否则启动时会 `SystemExit` 并在运行窗口里说清楚原因（`logwindow.hold`
会把窗口按住，见下）。

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
- **`pymysql` 要显式列进 `hiddenimports`**：它按 conf.json 里的 charset 在运行时挑
  编解码器，静态分析看不见，漏了的表现是「exe 一连库就 `LookupError: unknown encoding`」。
- **`cryptography` 是唯一一个带 C 扩展的依赖**（Cookie 代理用它做 AES-GCM）。
  PyInstaller 自带 hook，不用手写 hiddenimports；**别往 `excludes` 里加它**，
  排掉的表现是 exe 里 Cookie 代理静悄悄失效（`secretbox.available()` 返回 False），
  不报错，只是每次进外部站点都要重登。
- 脚本自己就叫 `pyinstaller.bat`，所以里面必须写 `python -m PyInstaller`——
  裸写 `pyinstaller` 会被 cmd 解析成这个脚本本身，死循环。
- **发布目录只放 Portal.exe**：`conf.json` 不从本机拷，那里面是这台机器自己的口令和数据。
