# 把你的系统接进单点登录

主页和业务系统不在同一个域名下，Cookie 天然带不过去。所以走的是「一次性票据」这套：
URL 里只出现一张 60 秒过期、用一次就废的票，真正的身份数据走**服务端到服务端**的 HTTP，
中间不经过浏览器。

```
浏览器                     主页 / 认证中心 (9920)          你的系统 (比如 8801)
  │                              │                              │
  │ 点导航卡片                   │                              │
  ├─ GET /sso/authorize ────────▶│                              │
  │        ?client_id=crm        │ 有主页会话？                 │
  │                              │  没有 → 302 回登录页，登完再回来
  │                              │  有   → 发一张票             │
  │◀── 302 你的系统/sso/callback?ticket=ST-xxx ──────────────────│
  ├─ GET /sso/callback?ticket=ST-xxx ──────────────────────────▶│
  │                              │◀── POST /sso/validate ───────┤  后端对后端
  │                              │     client_id+secret+ticket  │  浏览器看不到
  │                              ├── 用户信息 ─────────────────▶│
  │◀── 302 /  + 你自己的会话 Cookie ─────────────────────────────┤
  │                              │                              │
  └─ 进系统，一次账号密码都没输过                                 │
```

## 三步接入

### 0. 先注册你的系统

在 `server` 目录：

```bash
python manage.py addclient crm --name "客户管理系统" \
    --redirect-uri http://192.168.1.20:8080/sso/callback \
    --logout-uri  http://192.168.1.20:8080/sso/logout-notify \
    --home-url    http://192.168.1.20:8080/
```

会打印出 `client_id` 和 `client_secret`。secret 只在你的**服务端**用，别写进前端 JS。

`--redirect-uri` 必须和你系统实际用的回调地址一字不差（含端口、含路径、不含多余斜杠），
认证中心是精确匹配的。可以写多条（比如内网一条、外网一条）。

### 1. 没登录就去要票

```
302 → {SSO_BASE}/sso/authorize?client_id=crm&redirect_uri={你的回调地址}&state={随机串}
```

`redirect_uri` 可以省略，省略时用注册的第一条。`state` 原样带回来，用来防 CSRF。

### 2. 回调里拿票换身份

收到 `GET /sso/callback?ticket=ST-xxx&state=xxx`，在**后端**发一次请求：

```
POST {SSO_BASE}/sso/validate
Content-Type: application/json          （表单也收，PHP/老框架方便）

{ "client_id": "crm", "client_secret": "...", "ticket": "ST-xxx" }
```

返回：

```json
{
  "ok": true,
  "user": {
    "username": "zhangsan",
    "display_name": "张三",
    "email": "zhangsan@example.com",
    "roles": ["admin", "ops"]
  },
  "sid": "s-xxxxxxxx",
  "session_expires_at": 1757000000.0
}
```

拿到 `user` 就按你系统原来的方式建会话（Session / JWT / 随便什么），
**把 `sid` 一起存下来**，下一步要用。

票据校验失败一律 401，原因不区分：无效、过期、已经用过、client 对不上，都是 401。

### 3. 接单点登出（可选，但建议做）

主页点退出时，认证中心会向你注册的 `logout_uri` 发：

```
POST {你的 logout_uri}
{ "sid": "s-xxxxxxxx", "client_id": "crm", "secret": "...", "nonce": "..." }
```

先用 `hash_equals` / `timingSafeEqual` 这类定长比较校验 `secret`，
再把所有 `sso_sid == sid` 的本地会话删掉。

没做这步也能跑，只是主页退出后，你系统里的会话还会活到自己设的过期时间。

反过来，你系统里的「退出」想把主页一起退掉，就跳：

```
{SSO_BASE}/sso/logout?client_id=crm
```

退完会回到你注册的 `home_url`。

## 三个可以直接跑的示例

先在 `server` 目录按上面注册对应的 client，把打印出的 secret 填进文件顶部，然后：

| 语言 | 启动 | 端口 |
| --- | --- | --- |
| Python / FastAPI | `python examples/python-demo/app.py` | 8801 |
| Node（零依赖） | `node examples/node-demo/server.js` | 8802 |
| PHP（零依赖） | `php -S 127.0.0.1:8803 examples/php-demo/index.php` | 8803 |

跑起来后在主页加一张卡片，地址填示例系统的地址，「免登录跳转」选上对应的系统，
点一下就能看到效果。

## 几条不要省的

- **client_secret 只能待在服务端。** 一旦进了前端 JS，任何人都能拿它换身份。
- **redirect_uri 精确匹配。** 放松成前缀匹配，别人就能用
  `http://你的域名.evil.com/` 这种地址把票骗走。
- **票只用一次。** 换完就作废是认证中心保证的，你这边不用做去重。
- **生产走 https。** 然后把 `server/.env` 里的 `COOKIE_SECURE` 改成 `true`。
  注意反过来：http 下开了这个开关，浏览器会直接丢掉登录 Cookie。
- **别拿 `sid` 当身份凭证。** 它只是个会话编号，用来做登出联动，
  不要接受浏览器传来的 `sid` 直接登录。
