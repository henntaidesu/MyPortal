# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

（本仓库的代码注释、文档、CLI 输出均为中文，新增内容请保持一致。）

## 这是什么

一个「门户 Portal：系统导航 + 单点登录」：前端是导航卡片页（Vue 3 + Vite，9920），后端是认证中心
（FastAPI + MySQL，9921）。点卡片可以免登录直接进已接入的业务系统。

## 常用命令

```bash
# 一键启动（Windows，装依赖 + 建库建表 + 建默认管理员 + 起前后端 + 开浏览器）
start.bat

# 后端：必须在 backend/ 目录下执行，代码用的是相对包 app
cd backend
pip install -r requirements.txt
# 把 conf.ini 里 [database] 填对（首次运行会自动生成 conf.ini），库和表都会自动建
python manage.py init        # 建库 + 建表 + 建默认管理员 admin/admin（仅首次）
python -m app.main           # http://localhost:9921

# 前端：必须在 webside/ 目录下执行
cd webside
npm install
npm run dev                  # http://localhost:9920
npm run build                # 产物 webside/dist，后端会自动托管

# 打包成单文件 exe（在仓库根目录执行）
pyinstaller.bat              # 产物 Releases\<版本>\Portal.exe
```

账号 / 业务系统管理都在 `backend/manage.py`：`users` `adduser` `passwd` `rename` `disable`
`enable` `deluser` `clients` `addclient` `delclient`。账号那几项页面上也有（见下），
命令行这套的意义是不看会话、不看角色，管理员把自己锁在门外时还能救。注册业务系统：

```bash
python manage.py addclient crm --name "客户管理系统" \
    --redirect-uri http://192.168.1.20:8080/sso/callback \
    --logout-uri  http://192.168.1.20:8080/sso/logout-notify \
    --home-url    http://192.168.1.20:8080/
```

**本仓库没有测试、没有 linter、没有 CI**。改动靠手动跑起来验证：`start.bat` 起前后端，
浏览器里走一遍登录 → 点卡片 → `/sso/authorize` 跳转。SSO 全链路（拿票换身份、登出
广播）要一个真实业务系统配合，仓库里不带示例。

## 架构要点

### 两个进程，两种同源方式

开发态：vite(9920) 把 `/api` 和 `/sso` 代理到后端(9921)，`changeOrigin: false`——浏览器看到
的是同源，Cookie 才能带上。部署态：`npm run build` 后由 FastAPI 直接托管 `webside/dist`，
本来就同源。**两种模式下都不需要配 CORS**，新增接口时前缀必须落在 `/api` 或 `/sso`，
否则 dev 下代理不到。

`StaticFiles` 在 [backend/app/main.py](backend/app/main.py) 里**挂在最后**，接口路由先匹配，
剩下的才交给静态站点。新增路由要在 `app.mount('/')` 之前 include。

### SSO 是 CAS 风格的一次性票据，不是 OAuth

```
点卡片 → GET /sso/authorize?client_id=xxx
       → 没有门户会话就 302 回 /?next=... 登录，登完原样跳回来
       → 有会话就签一张 60 秒、只能用一次的票，302 带票跳业务系统
       → 业务系统后端 POST /sso/validate（client_id + client_secret + ticket）换用户信息
       → 业务系统建自己的会话
```

URL 里只出现票据，身份数据走服务端到服务端。要给别的系统对接，直接把
[docs/对接文档.md](docs/对接文档.md) 发过去，那份是自包含的。

七张表都在 [backend/app/db.py](backend/app/db.py) 的 `SCHEMA` 里：`users`、
`sessions`（门户会话）、`tickets`（一次性票）、`session_clients`（这个会话登过哪些系统，
用于单点登出广播）、`clients`（业务系统注册表）、`settings`（运行期配置）、
`nav`（每人一行的导航数据，整份 JSON）。

### 不能放松的安全不变量

改这几处前先想清楚，注释里也写了原因：

- **`redirect_uri` 精确匹配白名单**（[clients.py](backend/app/clients.py) `check_redirect_uri`）。
  改成前缀匹配或放行任意地址，票据就能被诱导送到别人服务器。
- **`next` 参数只接受站内路径**（前端 [auth.js](webside/src/auth.js) `safeNext`、
  后端 [sso.py](backend/app/routers/sso.py) `authorize`）。放开就是开放重定向。
- **`/sso/logout` 的回跳地址只取注册时的 `home_url`**，不接受调用方自带地址。
- **票据先删再校验**（`consume_ticket`），重放拿不到第二次。
- **令牌只存 sha256 指纹**（`token_fingerprint`），库泄露也换不回可用 Cookie。
- **登录失败不区分原因**，用户不存在时也跑一次同开销的哈希，防时序探测。
- `client_secret` 绝不下发前端；`/api/sso/clients` 只返回 id 和名字。
- **不能停用/删除自己，也不能动最后一个还启用着的管理员**（停用、删除、摘 admin 角色都挡，
  [users.py](backend/app/routers/users.py) `_guard_last_admin`）。放开就能一步把所有人关在门外。
- **`/api/password` 不验原密码、`/api/profile` 改名也不验**（产品要求，两处注释里都写了）。
  所以这两个接口的安全性完全压在会话 Cookie 上：谁拿到会话，谁就能改掉这个账号的密码和用户名。
  别再往这两条路上加别的权限动作。会话本身的防线还在——Cookie 是 httponly、库里只存指纹、
  登录有限流。（原来改名要验一次密码，但改密码都不验了，先改密码就能过那道校验，挡不住人只挡手。）
- **管理员重置别人口令后，把那个人的会话全踢掉并广播登出**，免得拿旧口令换来的会话还在用。
- **`/api/nav` 的 `user_id` 只从会话里取**（[nav.py](backend/app/routers/nav.py) `_me`），
  接口上不接受任何指定用户的参数。加一个就等于谁都能读写别人的导航。
- **用户名只走白名单**（[db.py](backend/app/db.py) `validate_username`，字母数字和 `_.-`）。
  它会进 URL、日志，还会原样发给业务系统当用户标识；中文写在 `display_name` 里。

### 会踩的坑

- **`COOKIE_SECURE` 反向陷阱**：https 部署必须开，但 http 环境下开了浏览器会**直接丢掉**
  登录 Cookie，表现为登录后立刻又变成未登录。
- **门户必须挂在域名根路径**：[NavCard.vue](webside/src/components/NavCard.vue) 里的 `href`
  是硬编码的绝对路径 `/sso/authorize`。挂子路径要改这里。
- **管理端接口一律走 [deps.py](backend/app/deps.py) 的 `require_admin`**（roles 里要有 admin）——
  `/api/settings/*` 和 `/api/users/*` 整两组都在里面，页面上「设置 → 用户管理 / 系统配置」
  两页也是照它藏的。这是仓库里唯一一处角色校验。`manage.py init` 会建一个默认管理员 `admin`/`admin`
  （[db.py](backend/app/db.py) 的 `DEFAULT_ADMIN` / `DEFAULT_PASSWORD`），
  口令没改过时 `manage.py` 和服务启动都会打警告——这是故意反复提醒，别去掉。
  万一 admin 账号全没了，命令行那套始终还能用。
- **配置值的合法性只在 [settings.py](backend/app/settings.py) `validate` 里把关**，
  页面和命令行都走它。像 `session_ttl_hours=0`、浏览器不收的 Cookie 名，
  都能把所有人挡在门外，所以每项都有取值范围。
- **start.bat 只能用 ASCII**：cmd 按系统 ANSI 代码页（936）解析 .bat，
  UTF-8 中文会被逐字节错位配对，`rem` 注释的后半截会被当成命令执行。
- **登录限流是进程内内存**（[deps.py](backend/app/deps.py) 的 `_fails` dict）。
  用多 worker 起 uvicorn 会让限流失效，目前设计就是单进程。
- **业务系统注册表改了不用重启**：它在 MySQL 的 `clients` 表里，
  [clients.py](backend/app/clients.py) 带 5 秒缓存，`manage.py addclient` 改完最多 5 秒生效；
  读表失败会沿用上一次的结果而不是让服务崩掉。
- **库是自动建的**（[db.py](backend/app/db.py) `ensure_database`，启动时跑）。库名会拼进
  `CREATE DATABASE` 的 DDL（标识符不能参数化），所以先过一道白名单正则；账号没有建库
  权限时会回落去查 `information_schema` 确认库在不在，在就继续用，不在才报错。
- **MySQL 密码不能带中文**：MySQL 协议按 latin-1 传密码，非 latin-1 字符会在 PyMySQL 里
  炸成 `UnicodeEncodeError`，[db.py](backend/app/db.py) `_new_conn` 提前拦了一道给人话提示。
- **conf.ini 不做变量插值、也不认行尾注释**：不然密码里的 `%` 和 `#` 会被 configparser
  吃掉，表现成「密码不对」。改 [config.py](backend/app/config.py) 的 `_load` 时注意。
- **连接池取连接会 ping**：MySQL 默认 `wait_timeout` 8 小时掐掉闲置连接，
  不 ping 的话隔夜第一个请求必报 `MySQL server has gone away`。
- 目录名是 `webside`（不是 website），别顺手改。
- `backend/conf.ini` 含数据库密码，已在 .gitignore 里，仓库里**不该**出现它。
  没有 .example 模板文件，缺文件时由 config.py 的 `_TEMPLATE` 现生成一份。

### 导航数据存在 MySQL，localStorage 只是缓存

导航卡片存在 `nav` 表里，一人一行、整份 JSON（`{title, theme, items[]}`），
所以换台机器登进来就能看到自己那份。接口是 `/api/nav` 的 GET / PUT
（[nav.py](backend/app/routers/nav.py)），前端这一侧全在
[webside/src/store.js](webside/src/store.js) 的 `pull()` / `push()` 两个函数里，
其余代码不关心数据从哪来——这是该文件刻意保持的边界。

- **`user_id` 只从会话里取，接口上没有任何指定用户的参数。** 放一个进来就等于
  谁都能读写别人那份导航。管理端也没有改别人导航的入口，这份数据不归管理端管。
- **localStorage 退成缓存**：进页面先画缓存不等接口，后端连不上时照常能看能改。
  改动一发生就打一个 `portal-nav:u<id>:dirty` 标记，推成功才清掉；
  下次 `bindUser` 看到标记就**先推后拉**，免得把本机没同步的改动冲掉。
  页面底部那行「连不上后端，改动暂时只存在本机」就是 `sync.offline`。
- **冲突是后写的盖先写的**，不做合并。自己给自己看的一页东西，为它做合并不值当。
- **老用户的数据是自动搬上去的**：`/api/nav` 返回 `data: null`（后端还没有这份）时，
  前端把本机缓存推上去。别去掉这条分支，不然老用户第一次登录会看到一片空。
- **卡片里有哪些字段后端不管**（`_clean` 只看标题、主题、条数和体积）：让后端跟着校验，
  卡片上加个字段就得两头一起改。加字段只改 store.js 的 `normalize()`。
- **分区键用用户 id（`portal-nav:u<id>`）不是用户名**：用户名页面上就能改，按名字存的话
  改完名连本机缓存都对不上了。所以 `/api/me` 要下发 `id`
  （[auth.py](backend/app/routers/auth.py) 的 `_public`），`bindUser` 收的是整个 user 对象。
  本机老键一站站往新键上搬，`takeOverKey` 只在新键还空着时才搬：
  `home-nav:<名字>`（改叫 Portal 之前）→ `portal-nav:<名字>`（按名字分区那版）→ `portal-nav:u<id>`。

后端管这几件事：用户/会话（`/api/login` `/api/me` `/api/logout` `/api/password` `/api/profile`）、
用户管理（`/api/users*`，仅 admin）、导航数据（`/api/nav`，只碰自己那份）、
运行期配置（`/api/settings*`，仅 admin）、SSO（`/sso/authorize` `/sso/validate` `/sso/logout`）、
站点图标代理（`/api/icon`，需登录，替调用方发请求所以不对匿名开放）。

## 配置分两层

**`backend/conf.ini`**（[config.py](backend/app/config.py) 读，标准库 configparser）只放
「连上数据库之前就必须知道」的东西：`[database]` 连接信息和 `[server]` 监听地址端口。
没有这个文件时 config.py 会用内置模板生成一份然后直接退出，提示去填 `[database]`——
所以第一次跑 `manage.py init` 或 `start.bat` 会「失败」一次，这是设计好的。
仓库里没有 .example 模板文件，模板就是 config.py 里的 `_TEMPLATE` 常量。

**其余全在数据库的 `settings` 表**（[settings.py](backend/app/settings.py)）：会话有效期、
Cookie 名和 `cookie_secure`、票据有效期、登出通知超时、登录限流。带 5 秒缓存，不用重启。
清单、默认值、取值范围和副作用提示都在 `DEFAULTS` 的 `Spec` 里，`init_db` 会把它补进表中。
两个入口：命令行 `manage.py settings` / `manage.py set <名字> <值>`，以及门户页面右上角
齿轮按钮里的「系统配置」页。齿轮人人都有（「我的账号」谁都能改），
但「系统配置」和「用户管理」两页只有 admin 看得见。

加新配置项只改 `DEFAULTS` 一处：命令行、接口、设置页都是照着它渲染的。

`DIST_DIR` 是算出来的不是配的：挂静态站点时就要用上，那会儿还不该去碰数据库。

> **别在 `with db.connect()` 里面读 settings。** 取值自己要占一条池化连接，
> 在已经持有连接时再调，池满会互相等死。`create_session` / `issue_ticket` 都是先取值再开连接。

## 打包成 exe

`pyinstaller.bat` + [portal.spec](portal.spec)，产物是单文件 `Releases\<版本>\Portal.exe`
（35 MB 上下），发布目录里**只有这一个文件**。入口是 [backend/main.py](backend/main.py)：
**不带参数就起服务，带参数就是 manage.py 的那套子命令**（`Portal.exe init` / `adduser` /
`addclient` …）。合成一个 exe 是因为拆两个要多背一份运行时，而且现场十有八九只拷走
其中一个，到了那边建不了账号。

图标三处同一张：exe 图标、托盘图标、运行窗口图标都用门户网页的 favicon
（`webside/public/favicon.ico` / `favicon.png`，指南针，就是 index.html 里 `<link rel=icon>`
指的那两个文件）。换图标只换这两个文件，spec 和 [tray.py](backend/app/tray.py) 的
`icon_path` 会跟着走。

### 双击是桌面程序，命令行还是命令行

exe 打成 **windowed（`console=False`）**：双击不弹 CMD 黑框，起来的是一个运行窗口
（[logwindow.py](backend/app/logwindow.py)）实时显示 uvicorn 日志，点 X 问「收进托盘 /
退出程序」，收进托盘后认证中心继续在后台跑，右下角托盘图标
（[tray.py](backend/app/tray.py)）能再打开窗口、打开门户、优雅退出。

这几条是踩出来的，改之前先读：

- **别改回 `console=True` + `hide_console`。** PyInstaller 那个 `hide_console='hide-early'`
  靠 `ShowWindow(GetConsoleWindow())` 藏窗口，而 Win11 默认终端是 Windows Terminal，
  拿到的是个代理窗口——实测黑框从启动到退出全程挂着，等于没做。
- **windowed 的代价：进程启动时没有控制台，`sys.stdout/stderr` 全是 None。**
  子命令得自己去借一个，见 [winconsole.py](backend/app/winconsole.py)。
- **借控制台不能用 `AttachConsole(ATTACH_PARENT_PROCESS)`。** onefile 跑起来是两个进程，
  干活的是引导器 fork 的子进程，它的「父进程」是引导器、引导器自己也没控制台，
  必然 `ERROR_INVALID_HANDLE`。所以要顺着进程链一路往上试到那个 cmd。
- **口令输入改成 Tk 小框。** cmd 不等 GUI 子系统的进程，提示符早还回去了，
  这时候再 `getpass` 就是和人抢同一个输入缓冲，字符两边各拿一半。
  `manage.py` 的 `_ask_password` 靠 `winconsole.can_prompt()` 分流，源码态照旧走 getpass。
- **重定向的情况别去动标准流**：`Portal.exe clients > out.txt` 时句柄是有效的，
  Python 自己已经把 `sys.stdout` 建好了，这时再 `open('CONOUT$')` 会把输出写进控制台、
  文件留个空的。`attach_parent` 只接管那几个 None 掉的。
- **`logwindow` 的 `_Tee` 必须能接受 `base=None`**，windowed 下传进来的就是 None。
  它得是个合法文件对象：uvicorn 配日志时会问 `isatty()`，logging 会调 `write`/`flush`，
  哪个抛异常都能把服务带崩。
- **启动失败时要把窗口按住**（`logwindow.hold`）。`sys.exit('去填数据库密码')` 那句提示
  是解释器退出时才打的，那会儿窗口早跟着进程没了——没有控制台可以退，双击的人
  只会看到「闪一下，什么都没有」。
- **tkinter / pystray / Pillow 不在 `backend/requirements.txt` 里**，只有 `pyinstaller.bat`
  打包前会装。源码态起服务不该为了一个托盘图标去装 pystray，所以这三个模块里的
  import 全写在函数里，缺了就静默跳过。spec 的 `excludes` 里**不能**再排 `tkinter` 和 `PIL`。

冻结后路径规则全在 [config.py](backend/app/config.py) 的 `FROZEN` 分支里，改之前先想清楚：

- **`conf.ini` 绝不能打进 exe。** 打进去的话每次启动都被临时解压目录里的那份盖掉，
  改了密码等于白改。所以 `BASE_DIR` 在冻结态取的是 `sys.executable` 所在目录，
  不是 `sys._MEIPASS`。exe 首次运行会在自己旁边生成 conf.ini 然后退出，和源码态一个流程。
- **前端打进 exe**（解压在 `_MEIPASS/webside`），但 exe 同级放一个 `webside` 目录就能盖掉它，
  换前端不用重新打包。
- **spec 里用 `SPECPATH` 定位项目根，不要用 `os.getcwd()`**：从别的目录调起来会算错，
  表现成「前端悄悄没打进 exe」。dist 不存在时 spec 直接 `SystemExit`，不留只警告的余地——
  少了前端的 exe 照样能跑能登录，只是首页一片空，到现场才发现就晚了。
- **spec 里不用 `collect_all`。** 它会把 `fastapi.testclient`、`pydantic.mypy`、
  `anyio.pytest_plugin` 这类可选子模块一并列成隐藏导入，在 anaconda base 这种什么都装了的
  环境里会顺藤摸瓜拖出 pytest / IPython / Qt，最后以「multiple Qt bindings」构建失败。
  依赖全是纯 Python，靠静态分析加自带 hook 就够，只有 uvicorn 那几个按字符串名字导入的
  协议实现要手写进 `hiddenimports`。
- 脚本自己就叫 `pyinstaller.bat`，所以里面必须写 `python -m PyInstaller`——
  裸写 `pyinstaller` 会被 cmd 解析成这个脚本本身，死循环。
- **发布目录只放 Portal.exe**，对接文档不往里拷（要发就从仓库的 `docs/` 发）。
  `conf.ini` 既不打进 exe 也不从本机拷——本机那份带着真实数据库密码。
