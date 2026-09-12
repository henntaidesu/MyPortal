# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

（本仓库的代码注释、文档、CLI 输出均为中文，新增内容请保持一致。）

## 这是什么

一个「门户 Portal：系统导航 + 单点登录」：前端是导航卡片页（Vue 3 + Vite，9920），后端是认证中心
（FastAPI + SQLite，9921）。点卡片可以免登录直接进已接入的业务系统。

## 常用命令

```bash
# 一键启动（Windows，装依赖 + 建首个账号 + 起前后端 + 开浏览器）
start.bat

# 后端：必须在 backend/ 目录下执行，代码用的是相对包 app
cd backend
pip install -r requirements.txt
python manage.py init        # 建库 + 建第一个账号（仅首次）
python -m app.main           # http://localhost:9921

# 前端：必须在 webside/ 目录下执行
cd webside
npm install
npm run dev                  # http://localhost:9920
npm run build                # 产物 webside/dist，后端会自动托管
```

账号 / 业务系统管理都在 `backend/manage.py`：`users` `adduser` `passwd` `disable` `enable`
`deluser` `clients` `addclient` `delclient`。注册业务系统：

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

URL 里只出现票据，身份数据走服务端到服务端。三张表的关系在
[backend/app/db.py](backend/app/db.py)：`sessions`（门户会话）、`tickets`（一次性票）、
`session_clients`（这个会话登过哪些系统，用于单点登出广播）。

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

### 会踩的坑

- **`COOKIE_SECURE` 反向陷阱**：https 部署必须开，但 http 环境下开了浏览器会**直接丢掉**
  登录 Cookie，表现为登录后立刻又变成未登录。
- **门户必须挂在域名根路径**：[NavCard.vue](webside/src/components/NavCard.vue) 里的 `href`
  是硬编码的绝对路径 `/sso/authorize`。挂子路径要改这里。
- **登录限流是进程内内存**（[deps.py](backend/app/deps.py) 的 `_fails` dict）。
  用多 worker 起 uvicorn 会让限流失效，目前设计就是单进程。
- **`clients.json` 改了不用重启**，按 mtime 自动重载；解析失败会沿用上一版而不是崩掉。
- 目录名是 `webside`（不是 website），别顺手改。
- `backend/data/`、`backend/.env`、`backend/clients.json` 是运行时数据，已在 .gitignore 里。

### 导航数据存在浏览器里

导航卡片存 `localStorage`，按用户分 key（`portal-nav:<用户名>`，改名前的 `home-nav*` 旧键首次读取时自动接管），**没有进后端**，
所以换台机器要重配。要搬到后端，只改 [webside/src/store.js](webside/src/store.js) 里的
`read()` / `write()` 两个函数，其余代码不用动——这是该文件刻意保持的边界。

后端只管三件事：用户/会话（`/api/login` `/api/me` `/api/logout` `/api/password`）、
SSO（`/sso/authorize` `/sso/validate` `/sso/logout`）、站点图标代理（`/api/icon`，需登录，
替调用方发请求所以不对匿名开放）。

## 配置

全部集中在 [backend/app/config.py](backend/app/config.py)，可用环境变量或 `backend/.env` 覆盖
（内置了十行的极简 .env 读取，没依赖 python-dotenv）。可配置项清单见
[backend/.env.example](backend/.env.example)。
