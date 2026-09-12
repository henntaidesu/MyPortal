# 系统主页导航 + 单点登录

把散落的各个系统入口收拢到一个页面，**登录一次，点卡片直接进系统，不用再登第二次**。

前端 Vue 3 + Vite（9920），认证中心 FastAPI + SQLite（9921）。

```
HomePage/
├── start.bat     双击启动（装依赖 + 建账号 + 起前后端 + 开浏览器）
├── webside/      前端导航页
├── server/       认证中心：用户、会话、票据
└── examples/     业务系统接入示例（Python / Node / PHP）+ 接入文档
```

## 启动

双击 `start.bat`。第一次会装依赖、并让你建第一个账号。

手动启动是两个进程：

```bash
# 认证中心
cd server
pip install -r requirements.txt
python manage.py init          # 只需第一次：建库 + 建第一个账号
python -m app.main             # http://localhost:9921

# 前端（另开一个窗口）
cd webside
npm install                    # 只需第一次
npm run dev                    # http://localhost:9920
```

前端的 `/api` 和 `/sso` 会代理到 9921，浏览器看到的是同源，不用配 CORS。

## 单点登录是怎么跑的

主页和业务系统不在同一个域名下，Cookie 带不过去，所以走一次性票据：

```
点卡片 → 认证中心签一张 60 秒、只能用一次的票 → 带票跳到你的系统
      → 你的系统后端拿票去换用户信息 → 建自己的会话 → 进系统
```

URL 里只出现那张票，真正的身份数据走服务端到服务端的请求，浏览器碰不到。

**接你自己的系统看 [examples/README.md](examples/README.md)**，里面有完整流程图、接口说明，
和三个能直接跑起来的示例（Python / Node / PHP，后两个零依赖）。

大致三步：

```bash
# 1. 注册你的系统，拿到 client_id 和 client_secret
cd server
python manage.py addclient crm --name "客户管理系统" \
    --redirect-uri http://192.168.1.20:8080/sso/callback \
    --logout-uri  http://192.168.1.20:8080/sso/logout-notify \
    --home-url    http://192.168.1.20:8080/

# 2. 在你的系统里写两个接口：
#    /sso/callback       拿 ticket → POST 认证中心 /sso/validate → 建本地会话
#    /sso/logout-notify  收到通知就销毁对应的本地会话（可选）

# 3. 在主页给这张卡片的「免登录跳转」选上它
```

## 账号管理

都在 `server` 目录：

```bash
python manage.py users                      # 看有哪些人
python manage.py adduser lisi --name 李四    # 加人（密码交互输入）
python manage.py passwd lisi                # 改密码
python manage.py disable lisi               # 停用，顺手把在线会话踢掉
python manage.py clients                    # 看注册了哪些系统
python manage.py addclient / delclient      # 加减系统
```

密码用标准库 scrypt 加盐哈希，会话令牌只在库里存 sha256 指纹，
库被看到也换不回可用的 Cookie。

## 用法

- 点面板里虚线的 **＋ 添加导航** 卡片新增
- 鼠标移到卡片上 → `✎` 编辑、`✕` 删除
- 直接拖动卡片调整顺序
- 右上角搜索框过滤，🌗 切换 跟随系统 / 浅色 / 深色，最右边点名字退出登录
- 标题「我的导航」可以直接点击改名
- 地址不用写 `http://`，粘 `192.168.1.10:8080` 也行
- 图标留空会自动抓站点图标，抓不到就显示彩色文字徽标；也可以填 emoji、文字或图片地址
- 编辑卡片时选上「免登录跳转」，卡片会带一个 `免登录` 角标，点开直接进系统
- 底部「导出备份 / 导入备份」迁移数据

## 数据

- **账号、会话、票据**：`server/data/sso.db`（SQLite）
- **导航内容**：浏览器 `localStorage`，按登录用户分开存（`home-nav:<用户名>`）

导航数据目前还是存在浏览器本地的，也就是说同一个人换台机器要重新配一遍。
想搬到后端的话，只改 [webside/src/store.js](webside/src/store.js) 里的 `read()` / `write()`
两个函数就够了，其余代码不用动。数据结构是一个扁平列表：

```json
{
  "title": "我的导航",
  "theme": "auto",
  "items": [
    { "id": "x1", "name": "客户管理", "url": "http://192.168.1.20:8080",
      "desc": "CRM", "icon": "", "sso": "crm" }
  ]
}
```

`sso` 填的是业务系统的 `client_id`，留空就是普通跳转。

## 部署

```bash
cd webside
npm run build            # 产物在 webside/dist
cd ../server
python -m app.main       # 后端顺带把 dist 托出去，同源，不用配 CORS
```

前面挂 Nginx 的话，把 `/`、`/api`、`/sso` 都反代到后端即可。
**主页必须挂在域名根路径下**（卡片跳的是绝对路径 `/sso/authorize`），
挂子路径要自己改 [webside/src/components/NavCard.vue](webside/src/components/NavCard.vue) 里的 `href`。

上了 https 记得把 `server/.env` 里的 `COOKIE_SECURE` 改成 `true`；
反过来，http 环境下开了它，浏览器会直接丢掉登录 Cookie。
可配置项都在 [server/.env.example](server/.env.example) 里。

## 文件

```
webside/src/
├── App.vue                 未登录显示登录页，登录后是导航面板
├── api.js                  跟后端说话的统一出口
├── auth.js                 登录状态
├── store.js                导航数据 + 增删改查 + 持久化（换后端只改这里）
├── utils.js                URL 补全、图标解析、配色
├── icons.js                图标抓取与缓存
├── styles.css              主题变量
└── components/
    ├── LoginView.vue       登录页
    ├── NavCard.vue         导航卡片（免登录跳转在这里拼 URL）
    ├── NavIcon.vue         图标（favicon / emoji / 文字徽标）
    └── CardDialog.vue      添加、编辑弹窗

server/
├── manage.py               命令行：建用户、改密码、注册业务系统
├── clients.json            业务系统注册表（首次启动自动生成模板，改了不用重启）
├── .env.example            可配置项
└── app/
    ├── main.py             组装路由 + 托管 dist
    ├── config.py           配置
    ├── db.py               用户、会话、票据的存储
    ├── security.py         scrypt 哈希、随机令牌
    ├── clients.py          业务系统注册表
    ├── deps.py             会话 Cookie、登录限流
    ├── notify.py           单点登出通知
    ├── icon.py             站点图标代理
    └── routers/
        ├── auth.py         /api/login /api/me /api/logout
        └── sso.py          /sso/authorize /sso/validate /sso/logout
```
