# 门户 Portal · 一个多用户导航页

把散落的各个系统入口收拢到一个页面：登录一次，点卡片就打开。
**每个人有自己的一份导航**，互相看不见。

前端 Vue 3 + Vite（9920），后端 Python / FastAPI（9921），数据在 **MySQL** 里。

```
Portal/
├── start.bat     双击启动（装依赖 + 起前后端 + 开浏览器）
├── webside/      前端导航页
└── backend/      Python 后端：托管页面 + 登录 + 读写数据库 + 代抓站点图标
```

## 先决条件

**一个连得上的 MySQL**（5.7+ / 8.x，或 MariaDB 10.2+）。库和表由程序自己建，
但连接账号要有 `CREATE DATABASE` 和建表的权限。连不上服务就起不来——
用户和导航全在库里，带着一个连不上的库把服务起起来，每个接口都会是 500。

## 启动

双击 `start.bat`，第一次会自动装依赖，并在 `backend/` 下生成一份 `conf.json`。
**第一次跑之前先把 `conf.json` 的 `database` 那一段填成你自己的 MySQL**，然后再起一次。

手动启动是两个进程：

```bash
# 后端
cd backend
pip install -r requirements.txt   # 只需第一次：fastapi + uvicorn + httpx + PyMySQL
python -m app.main                # http://localhost:9921

# 前端（另开一个窗口）
cd webside
npm install                       # 只需第一次
npm run dev                       # http://localhost:9920
```

前端的 `/api` 会代理到 9921，浏览器看到的是同源，不用配 CORS。

第一个管理员按 `conf.json` 的 `auth.bootstrap_admin` 建，默认 **`admin` / `admin`**，
**只在库里一个用户都没有时才建**。

> **登录后马上改口令**：右上角菜单 → 账号 → 改口令。改完之后可以把
> `conf.json` 里的 `bootstrap_admin` 整段删掉。
> 只要那一段还是 `admin` / `admin`，后端每次启动都会在日志里警告一句。

## 用法

- 导航按**分类**组织，一个分类画成一张大卡，底部虚线的 **＋ 新建分类** 加一个
- 分类名直接点着改；分类右上角 `＋` 往里加导航、`✕` 删掉整个分类（连里面的导航一起）
- 鼠标移到卡片上 → `✎` 编辑、`✕` 删除
- 拖卡片调顺序，**拖到别的分类上就换分类了**；拖分类左边的 `⠿` 把手调分类顺序
- 编辑卡片时也可以在「分类」下拉框里直接换分类
- 地址不用写 `http://`，粘 `192.168.1.10:8080` 也行
- 图标留空会自动抓站点图标，抓不到就显示彩色文字徽标；也可以填 emoji、文字或图片地址
- 卡片上的「门户代理」那一栏有三挡，见下面那节

右上角是当前登录的人，点开有：

- **账号** —— 改显示名、改口令、看绑了哪些单点登录、把自己所有设备踢下线
- **用户管理**（只有管理员看得到）—— 建号、改角色、停用、重置口令、踢下线、删除
- **退出登录**

> 页面上只有分类和卡片：没有标题栏、没有主题切换、没有搜索，也没有导入导出。
> 深浅色跟随系统。登录状态按 `session_hours`（默认 168 小时）自动过期。

## 多用户

- **每个账号一份导航**，互相看不见，也搬不了。
- **角色只有两种**：管理员和普通用户。管理员能管所有账号，但**看不到别人的导航**——
  那是数据，不是配置，没有「替别人改一下」这个入口。
- **最后一个管理员不能降级、停用或删除**，不然就没人能管用户了。
- **删用户会连他名下的导航一起删**，删了就没了。
- 改口令、被停用、被踢下线，这个人**所有设备上的登录立刻失效**（别人不受影响）。

## 单点登录（OIDC）

支持任何讲标准 OIDC 的身份提供方：Keycloak、Authentik、Casdoor、Logto、
Okta、Auth0、Google 等。在 `conf.json` 里填：

```json
"oidc": {
  "enabled": true,
  "issuer": "https://sso.example.com/realms/main",
  "client_id": "portal",
  "client_secret": "……",
  "scopes": "openid profile email",
  "redirect_url": "https://portal.example.com/api/auth/oidc/callback",
  "auto_create_user": true,
  "username_claim": "preferred_username",
  "admin_claim": "groups",
  "admin_claim_value": "portal-admin"
}
```

- IdP 那边把**回调地址**登记成 `<你的门户地址>/api/auth/oidc/callback`。
  `redirect_url` 留空的话程序会按请求的 Host 现拼一个，但挂在反代后面时多半拼得不对，
  建议写死。
- `auto_create_user` 打开时，IdP 里认过的人第一次登录自动建号（普通用户）。
  关掉的话得由管理员先建好同名账号，OIDC 只负责认人。
- `admin_claim_value` 填了之后，那个 claim 里带这个值的人自动是管理员；
  留空就完全由门户自己的用户表说了算。
- **认人只认 IdP 给的 `sub`**，所以在 IdP 那边改名、改邮箱都不会把人变成另一个账号。
- 接了单点登录之后通常要把 `auth.allow_local_login` 改成 `false`，
  不然本地口令就是一条绕开 IdP 的后门。（两条都关掉的话谁都进不来，服务会拒绝启动。）

## 门户代理

卡片编辑框里那一栏，让门户替浏览器把请求转出去——适合「门户这台机器够得着、
但你人在外面够不着」的系统。

| 卡片上选 | 干什么 |
| --- | --- |
| 关闭 | 直接连原地址 |
| 开启 · 直接转发 | 只转卡片这一台机器，正文一个字不改。内网后台、NAS、路由器管理页用它 |
| 开启 · 整站 | 一张卡片转一整族域名，还改写页面里的地址。公网站点用它 |

改完刷新即刻生效，不用重启。**只有卡片的主人能通过它转发**，
别人拿到卡片 id 也打不开。

## conf.json

**只有配置，没有数据**（打包成 exe 后是 exe 同级的 `conf.json`）。
用户、导航、单点登录绑定关系都在 MySQL 里——**备份就是备份那个库**。

```json
{
  "server": { "host": "0.0.0.0", "port": 9921 },
  "database": {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "portal",
    "password": "改成你自己的",
    "name": "portal",
    "charset": "utf8mb4",
    "pool_size": 8
  },
  "auth": {
    "session_hours": 168,
    "cookie_secure": false,
    "allow_local_login": true,
    "bootstrap_admin": { "username": "admin", "password": "admin" }
  },
  "oidc": { "enabled": false }
}
```

- **整份只在启动时读一次，改完要重启。** 会变的东西（口令、用户、导航）都在库里，
  改库即刻生效，不用再动这个文件。
- **`bootstrap_admin` 只在库里一个用户都没有时才看。** 已经有人了就完全不管它——
  留着既不会重复建号，也不会把你在页面上改过的口令改回去。建完可以整段删掉。
- **`cookie_secure` 只有 https 部署才能开。** http 下开了浏览器会直接丢掉登录 Cookie，
  表现成「登录成功后立刻又变回未登录」。
- 文件里有数据库口令和 OIDC 的 `client_secret`，所以它在 .gitignore 里，别随手发给别人。
- 老版本留下的 `conf.ini` / `nav.json` 首次启动会被自动折进 `conf.json`，
  其中的导航会被搬进第一个管理员名下。**老文件不会被删**，确认没问题之后自己删。

浏览器的 `localStorage` 里还有一份导航，但那只是缓存（按账号分开存）：进页面先拿它
画一屏不用等接口，后端连不上时也照常能看能改，改动攒着，下次连上自动补传。
两个浏览器同时改的话，后按下的那边赢。

站点图标缓存在 `backend/icons/` 目录里，不进数据库，**整个删掉也没事**，
下次访问会自己重建重抓。

## 从单账号那一版升级

1. 先备份 `backend/conf.json`（里面有你全部的导航）。
2. 准备一个 MySQL，把 `database` 那一段填上。
3. 直接启动。程序会：
   - 建库建表；
   - 把 `conf.json` 里原来的 `auth.username` / `auth.password` 当成第一个管理员建出来
     （**你原来的用户名和口令继续能用**）；
   - 把 `conf.json` 里的 `nav` 整段搬到这个管理员名下，**原文件那一段留着不动**。
4. 登进去确认导航都在，然后可以把 `conf.json` 里的 `nav` 和 `auth.password` 删掉。

## 部署

```bash
cd webside
npm run build            # 产物在 webside/dist
cd ../backend
python -m app.main       # 后端顺带把 dist 托出去，同源，不用配 CORS
```

前面挂 Nginx 的话，把 `/` 和 `/api` 都反代到后端即可。
上了 https 记得把 `conf.json` 的 `cookie_secure` 改成 `true`。

**按单进程跑**：登录失败限流、导航改动计数、代理的目标表缓存都在进程内存里。
真要横着扩也能跑（会话是签名 Cookie，密钥在库里，多个实例共享得了），
只是限流会被摊薄、卡片改动最多晚 5 秒被别的实例看到。

### 打包成一个 exe

目标机器不想装 Python 和 Node 的话，在仓库根目录双击 `pyinstaller.bat`，
产物是 `Releases\<版本>\Portal.exe` 一个文件（前端已经打在里面）。

双击就能跑，没有子命令：它会在自己旁边生成 `conf.json` 和 `icons/`。
**但它需要一个连得上的 MySQL**——第一次跑起来会因为连不上库而停下，
把生成的 `conf.json` 里 `database` 那一段填对再跑一次。

exe 是 windowed 打的：双击不弹 CMD 黑框，起来的是一个运行窗口实时显示日志，
点 X 可以收进托盘继续在后台跑。要换前端而不想重新打包，把 `webside/dist`
的内容放到 exe 同级的 `webside` 目录即可。

## 文件

```
webside/src/
├── App.vue                 导航面板（一列分类 + 右上角的用户菜单）
├── api.js                  跟后端说话的统一出口
├── auth.js                 登录状态、有哪几条登录路、跳 OIDC
├── store.js                导航数据 + 增删改查 + 同步（换存储只改这里）
├── drag.js                 拖拽状态（拖卡片、拖分类）
├── utils.js                URL 补全、配色
├── icons.js                拼 /api/icon 的地址
├── styles.css              主题变量
└── components/
    ├── LoginView.vue       登录页（本地表单 + 单点登录按钮）
    ├── AccountDialog.vue   账号：显示名、改口令、踢掉所有设备
    ├── UsersDialog.vue     用户管理（管理员）
    ├── NavGroup.vue        一个分类 = 一张大卡
    ├── NavCard.vue         导航卡片
    ├── NavIcon.vue         图标（favicon / emoji / 文字徽标）
    └── CardDialog.vue      添加、编辑弹窗

backend/
├── main.py                 打包后的 exe 入口（开发时用不着）
├── conf.json               配置（首次运行自动生成，不进版本库）
└── app/
    ├── main.py             组装路由 + 托管 dist
    ├── config.py           conf.json 的读写，算各种路径
    ├── db.py               MySQL 连接池 + 建表
    ├── users.py            用户表：口令哈希、角色、OIDC 绑定
    ├── auth.py             会话签名 Cookie、登录校验、失败限流
    ├── oidc.py             OIDC 单点登录（授权码 + PKCE）
    ├── navstore.py         每人一份导航的读写与校验
    ├── icon.py             站点图标代理
    ├── iconcache.py        图标的磁盘缓存
    ├── proxy.py            门户代理：转发、首部与 Cookie 改写、WebSocket
    ├── proxyrewrite.py     整站模式：改写正文里的地址、域名白名单
    ├── proxyhook.py        整站模式：注入页面的那段运行时脚本
    ├── logwindow.py        打包后的运行窗口
    ├── tray.py             打包后的托盘图标
    ├── proxy_webside/      一个站一个文件的特化规则
    │   ├── _yahoo.py       两个雅虎站共用的域名清单
    │   ├── mercari.py      メルカリ
    │   ├── yahoo_auctions.py      ヤフオク!
    │   └── paypayfleamarket.py    PayPayフリマ
    └── routers/
        ├── auth.py         /api/me /api/login /api/logout /api/auth/oidc/*
        ├── account.py      /api/account：改自己的显示名、口令、踢自己下线
        ├── users.py        /api/users：用户管理（管理员）
        └── nav.py          /api/nav 的 GET / PUT

pyinstaller.bat             打包：构建前端 + 打出单文件 Portal.exe
portal.spec                 PyInstaller 配置（前端打进 exe，conf.json 留在外面）
```
