# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

（本仓库的代码注释、文档、CLI 输出均为中文，新增内容请保持一致。）

## 这是什么

一个简单的导航页：前端是导航卡片页（Vue 3 + Vite，9920），后端是一个 Python 进程
（FastAPI，9921），负责托管页面、一个账号的登录、读写导航数据、代抓站点图标。

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
- **`require_login` 是仓库里唯一一处权限校验**，`/api/nav` 和 `/api/icon` 两个 router
  都挂在 `dependencies` 上。`/api/me` 故意**不**要求登录（没登录返回 200 + `user: null`）：
  页面一起来就要问一次，回 401 会触发前端那条「会话过期」的通路，把「本来就还没登录」
  报成掉线。
- **`/api/icon` 要求登录**，因为它会替调用方发请求。但登录之后**不拦内网地址**——
  拦了就取不回 `192.168.x.x` 那些系统的 favicon，而门户存在的意义正是指向那些地址。

### 导航数据：分类 → 卡片两层，后端权威，localStorage 只是缓存

```
{ title, theme, groups: [ { id, name, items: [ { id, name, url, desc, icon } ] } ] }
```

一个分类在页面上画成一张大卡（[NavGroup.vue](webside/src/components/NavGroup.vue)）。
前端这一侧全在 [webside/src/store.js](webside/src/store.js) 的 `pull()` / `push()` 两个
函数里，其余代码不关心数据从哪来——这是该文件刻意保持的边界。

- **老的扁平 `items` 结构要一直认**：`normalize()` 见到顶层 `items` 就收进一个默认分类，
  后端 `navstore.clean` 也有同一条分支。去掉的话，升上来的人第一次打开是一片空。
- **拖拽状态在 [drag.js](webside/src/drag.js)，不在 store.js**：那边是要存盘的数据，
  这边纯粹是鼠标此刻在哪。也没做成 props 一层层往下传——分类大卡和卡片都要读它。
  不走 `dataTransfer`：它只能传字符串，而且 dragover 时读不出来，没法一边拖一边判断高亮。
- **搜索时禁拖**（`sortable` 为 false）：显示出来的下标和 `group.items` 里的真实下标对不上，
  拖了会错位。NavGroup 里那个 `rows` 就是为此把真实下标一起算出来的。
- **分类只有 `⠿` 把手可拖**，不是整个分类头：整张头都可拖的话，里面那个 contenteditable
  的分类名就没法用鼠标选字了。
- **进页面先画缓存不等接口**，后端连不上时照常能看能改。改动一发生就打一个
  `portal-nav:dirty` 标记，推成功才清掉；下次进来看到标记就**先推后拉**，
  免得把本机没同步的改动冲掉。底部那行「连不上后端，改动暂时只存在本机」就是 `sync.offline`。
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
