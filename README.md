# 门户 Portal · 一个简单的导航页

把散落的各个系统入口收拢到一个页面：登录一次，点卡片就打开。

前端 Vue 3 + Vite（9920），后端 Python / FastAPI（9921）。
**没有数据库**：配置和数据全在后端目录下的一个 `conf.json` 里。

```
Portal/
├── start.bat     双击启动（装依赖 + 起前后端 + 开浏览器）
├── webside/      前端导航页
└── backend/      Python 后端：托管页面 + 登录 + 读写 conf.json + 代抓站点图标
```

## 启动

双击 `start.bat`，第一次会自动装依赖。没有任何要预先填的东西——`conf.json`
用到的时候自己生成，默认账号 **`admin` / `admin`**。

手动启动是两个进程：

```bash
# 后端
cd backend
pip install -r requirements.txt   # 只需第一次：fastapi + uvicorn + httpx
python -m app.main                # http://localhost:9921

# 前端（另开一个窗口）
cd webside
npm install                       # 只需第一次
npm run dev                       # http://localhost:9920
```

前端的 `/api` 会代理到 9921，浏览器看到的是同源，不用配 CORS。

> **登录后马上改口令**：编辑 `backend/conf.json` 的 `auth.password`，然后重启。
> 只要还是默认口令，后端每次启动都会在日志里警告一句。

## 用法

- 导航按**分类**组织，一个分类画成一张大卡，底部虚线的 **＋ 新建分类** 加一个
- 分类名直接点着改；分类右上角 `＋` 往里加导航、`✕` 删掉整个分类（连里面的导航一起）
- 鼠标移到卡片上 → `✎` 编辑、`✕` 删除
- 拖卡片调顺序，**拖到别的分类上就换分类了**；拖分类左边的 `⠿` 把手调分类顺序
- 编辑卡片时也可以在「分类」下拉框里直接换分类
- 添加 / 编辑弹窗里的输入框**没有文字标注**，从上到下依次是
  分类、名称、地址、描述、图标（鼠标停一下有 tooltip）
- 地址不用写 `http://`，粘 `192.168.1.10:8080` 也行
- 图标留空会自动抓站点图标，抓不到就显示彩色文字徽标；也可以填 emoji、文字或图片地址

> 页面上只有分类和卡片：没有标题栏、没有主题切换、没有搜索、没有退出按钮，
> 也没有导入导出——要搬数据就拷 `conf.json`。
> 深浅色跟随系统（也可以在 `conf.json` 里把 `nav.theme` 写成 `light` / `dark` 钉死）。
> 登录状态按 `session_hours`（默认 168 小时）自动过期；要立刻让所有浏览器掉线，
> 改一下 `auth.password` 再重启。

## conf.json

配置和数据在同一个文件里（打包成 exe 后是 exe 同级的 `conf.json`）。
**备份就是拷走它，恢复就是拷回去。** 也可以直接用记事本改：

```json
{
  "server": { "host": "0.0.0.0", "port": 9921 },
  "auth": {
    "username": "admin",
    "password": "改成你自己的",
    "session_hours": 168,
    "cookie_secure": false
  },
  "nav": {
    "title": "我的门户",
    "theme": "auto",
    "groups": [
      {
        "id": "g1",
        "name": "业务系统",
        "items": [
          { "id": "x1", "name": "客户管理", "url": "http://192.168.1.20:8080",
            "desc": "CRM", "icon": "" }
        ]
      }
    ]
  }
}
```

- `server` 和 `auth` 启动时读一次，**改完要重启**；`nav` 是页面上随手在改的，
  存的时候只换 `nav` 那一段，服务跑着的时候手改口令也不会被盖掉。
- **改口令会让所有已登录的浏览器一起掉线**：会话 Cookie 的签名密钥是从口令算出来的。
  想把所有设备踢下线，改一下口令就行。
- **`cookie_secure` 只有 https 部署才能开。** http 下开了浏览器会直接丢掉登录 Cookie，
  表现成「登录成功后立刻又变回未登录」。
- 口令是明文存的，所以这个文件在 .gitignore 里，别随手发给别人。
- 老版本留下的 `conf.ini` / `nav.json` 首次启动会被自动折进 `conf.json`，
  确认没问题之后可以删掉。

浏览器的 `localStorage` 里还有一份导航，但那只是缓存：进页面先拿它画一屏不用等接口，
后端连不上时也照常能看能改，改动攒着，下次连上自动补传。
两个浏览器同时改的话，后按下的那边赢。

分类是后加的：`nav` 里如果是老的扁平 `items` 列表，第一次打开会自动收进一个默认分类。

站点图标缓存在 `backend/icons/` 目录里，**整个删掉也没事**，下次访问会自己重建重抓。

## 部署

```bash
cd webside
npm run build            # 产物在 webside/dist
cd ../backend
python -m app.main       # 后端顺带把 dist 托出去，同源，不用配 CORS
```

前面挂 Nginx 的话，把 `/` 和 `/api` 都反代到后端即可。
上了 https 记得把 `conf.json` 的 `cookie_secure` 改成 `true`。

### 打包成一个 exe

目标机器不想装 Python 和 Node 的话，在仓库根目录双击 `pyinstaller.bat`，
产物是 `Releases\<版本>\Portal.exe` 一个文件（前端已经打在里面）。

双击就能跑，没有子命令：它会在自己旁边生成 `conf.json` 和 `icons/`，
打开 http://localhost:9921 用 `admin` / `admin` 登录，然后去 `conf.json` 改口令。

exe 是 windowed 打的：双击不弹 CMD 黑框，起来的是一个运行窗口实时显示日志，
点 X 可以收进托盘继续在后台跑。要换前端而不想重新打包，把 `webside/dist`
的内容放到 exe 同级的 `webside` 目录即可。

## 文件

```
webside/src/
├── App.vue                 导航面板（就是一列分类）
├── api.js                  跟后端说话的统一出口
├── auth.js                 登录状态
├── store.js                导航数据 + 增删改查 + 同步（换存储只改这里）
├── drag.js                 拖拽状态（拖卡片、拖分类）
├── utils.js                URL 补全、配色、备份下载
├── icons.js                拼 /api/icon 的地址
├── styles.css              主题变量
└── components/
    ├── LoginView.vue       登录页
    ├── NavGroup.vue        一个分类 = 一张大卡
    ├── NavCard.vue         导航卡片
    ├── NavIcon.vue         图标（favicon / emoji / 文字徽标）
    └── CardDialog.vue      添加、编辑弹窗

backend/
├── main.py                 打包后的 exe 入口（开发时用不着）
├── conf.json               配置 + 导航数据（首次运行自动生成，不进版本库）
└── app/
    ├── main.py             组装路由 + 托管 dist
    ├── config.py           conf.json 的读写，算各种路径
    ├── auth.py             登录校验、签名 Cookie、失败限流
    ├── navstore.py         conf.json 里 nav 那一段的读写与校验
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
        ├── auth.py         /api/login /api/me /api/logout
        └── nav.py          /api/nav 的 GET / PUT

pyinstaller.bat             打包：构建前端 + 打出单文件 Portal.exe
portal.spec                 PyInstaller 配置（前端打进 exe，conf.json 留在外面）
```
