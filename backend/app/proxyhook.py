"""注入被代理页面的那段脚本：运行时拼出来的地址，在真要发请求的那一刻折回门户。

app/proxyrewrite.py 改的是**正文里写死的地址**，覆盖不到这几种：

    fetch(location.origin + '/v1/items')      // 拿 origin 现拼的，字面量里没有主机名
    new Image().src = path                    // path 是接口回来的根绝对地址
    el.innerHTML = '<img src="https://...">'  // 整段 HTML 是运行时才有的

所以再补一道：把 `fetch` / `XMLHttpRequest` / `WebSocket` / `Worker` 这些出口，
以及元素的 `src` / `href` 这些属性全包一层，地址过一遍 `toProxy` 再放行。
白名单之外的地址**原样放行**（照旧直连），和后端那道是同一条规矩。
例外是「点过去的页面」（`<a>` / `<form>` 的地址、`window.open`，走 `toNav`）：
那几处白名单外的也折进代理，由后端在请求那一刻查是不是公网地址再决定转不转。
`<a>` / `<form>` 只在**已经挂进文档**时才这么办：没挂进去的 `<a>` 多半是脚本拿来解析地址的，
见 `navIfAttached`。

## 有意做成这样

- **整段内联进 HTML，不做成外链脚本**：它必须比页面里任何一个脚本先跑完，
  外链多一次往返不说，`async`/`defer` 的次序也不好保证。几 KB 而已。
- **`location.href = '...'`、`location.replace(...)` 包不了，改从 navigate 事件拦**：
  `Location` 的属性是 unforgeable，脚本改不了；但 Navigation API 在真跳之前会先发
  navigate 事件，跨源的取消掉、换成代理地址重新跳（脚本里「地址栏与 Cookie」那段）。
  没有 Navigation API 的浏览器还是拦不住，靠正文改写覆盖（字面量那半截已经被换掉了），
  再兜不住就落到门户根路径上，由 app/proxy.py 末尾那条 Referer 兜底转回来。
- **Service Worker 直接掐掉**：它注册下来的作用域是 `/api/proxy/<id>/…`，
  会横在所有请求前面按自己那套改地址，和这里两套改法打架；而且卸载它得进
  浏览器设置，坏起来非常难查。
- **`document.domain` 赋值吞掉**：雅虎那几个站的老脚本会写
  `document.domain = 'yahoo.co.jp'`，在门户这个域名下赋值直接抛 SecurityError，
  整个脚本就断在那儿了。
- **`localStorage` / `sessionStorage` 换成带前缀的一层**（前缀是「门户用户 + 卡片」）：
  被代理的页面全在门户这一个源底下，这两样是整个源共用一份的。不分开的话，
  同一个浏览器里换个门户账号打开同一张（分享来的）卡片，页面读到的是上一个人存下的
  登录 token，直接就以上一个人的身份进去了——二手系统（FreeMarket）就是这么串的号。
  不同卡片之间也会互相读到、一句 `clear()` 把别人的（连门户自己的导航缓存）一起清掉。
  只管这两样：IndexedDB / Cache Storage 没分开；`storage` 事件里的 key 带着前缀；
  拿 `Storage.prototype.getItem.call(...)` 或者空白 iframe 绕过去的也拦不住。
"""
import json

# 元素上那些值是地址的属性。改 innerHTML 塞进来的节点由 MutationObserver 补一遍，
# 因为那条路不经过 setAttribute，也不经过属性 setter
_JS = r"""
(function () {
  'use strict';
  var C = __PORTAL_CONFIG__;
  if (window.__portal) return;

  /* 原生的那一个。下面会把 window.URL 包一层，内部解析一律用这份，
     免得绕回包装函数里去 */
  var NativeURL = window.URL;

  var ATTRS = ['src', 'href', 'action', 'formaction', 'poster', 'data', 'ping', 'manifest'];
  var SETATTRS = { src: 1, href: 1, action: 1, formaction: 1, poster: 1, data: 1, ping: 1, manifest: 1 };

  function allowed(host) {
    var h = String(host || '').toLowerCase().split(':')[0];
    if (!h) return false;
    for (var i = 0; i < C.allow.length; i++) {
      var d = C.allow[i];
      if (h === d || h.length > d.length && h.slice(-(d.length + 1)) === '.' + d) return true;
    }
    return false;
  }

  /* 门户上的一条代理地址 → 它对应的上游真实地址。不是代理地址就返回 null。
     参数要的只是 pathname / search / hash 三样，所以 location 也能直接递进来。
     后端那边的同一件事在 app/proxy.py 的 `_real_url`。 */
  function unproxy(u) {
    var p = u.pathname || '';
    if (p.indexOf(C.base) === 0) {
      var rest = p.slice(C.base.length), i = rest.indexOf('/');
      var sch = i < 0 ? '' : rest.slice(0, i);
      if (sch === 'http' || sch === 'https') {
        var body = rest.slice(i + 1), j = body.indexOf('/');
        var host = j < 0 ? body : body.slice(0, j);
        if (host) return sch + '://' + host + (j < 0 ? '/' : body.slice(j)) + u.search + u.hash;
      }
      return null;
    }
    if (p.indexOf(C.mount) === 0) {     /* 老形状：/api/proxy/<id>/<路径> */
      return C.scheme + '://' + C.host + '/' + p.slice(C.mount.length) + u.search + u.hash;
    }
    return null;
  }

  /* 当前页面对应的**真实**地址。相对地址要拿它当基准解析，
     拿 location.href 当基准的话，解析出来的是门户上那条代理路径，主机名就丢了 */
  function realHref() {
    return unproxy(location) || (C.scheme + '://' + C.host + '/' + location.search + location.hash);
  }

  function mount(proto, host, rest) {
    return C.base + proto + '/' + host + rest;
  }

  function toProxy(input) { return convert(input, false); }

  /* 「点过去的页面」（链接、表单、window.open）：白名单之外的主机也折回代理底下。
     能不能真转由后端在请求那一刻查（只放行公网地址，app/proxy.py 的 `_public`），
     这里只管别让浏览器绕过门户直连出去 */
  function toNav(input) { return convert(input, true); }

  function convert(input, nav) {
    if (input === null || input === undefined) return input;
    var raw = String(input);
    if (!raw || raw.charAt(0) === '#') return input;
    /* 已经是代理地址了。这一条必须在解析之前：相对地址是拿**上游**那个地址当基准解析的，
       `/api/proxy/<id>/…` 这种根绝对地址走到下面会被当成上游的路径，套成两层
       （正文里那些改写好的地址会被 MutationObserver 再摸一遍，正好撞上这条） */
    if (raw.indexOf(C.mount) === 0) return input;
    /* data:、blob:、mailto:、javascript:、ws: —— 有协议名而且不是 http(s) 的一律不碰 */
    if (/^[a-z][a-z0-9+.\-]*:/i.test(raw) && !/^https?:/i.test(raw)) return input;
    var abs;
    try { abs = new NativeURL(raw, realHref()); } catch (e) { return input; }
    if (abs.protocol !== 'http:' && abs.protocol !== 'https:') return input;
    var rest = abs.pathname + abs.search + abs.hash;
    if (abs.origin === location.origin) {
      if (abs.pathname.indexOf(C.mount) === 0) return input;   /* 已经在代理底下 */
      /* 门户自己这个 origin 上的根绝对地址：本来是上游的路径，被解析到门户头上了 */
      var me;
      try { me = new NativeURL(realHref()); } catch (e) { return input; }
      return mount(me.protocol.slice(0, -1), me.host, rest);
    }
    if (!nav && !allowed(abs.hostname)) return input;
    if (abs.username || abs.password) return input;   /* user@host 不接，和后端一样 */
    return mount(abs.protocol.slice(0, -1), abs.host, rest);
  }

  /* WebSocket 走同一条代理路径，主机名段里记的仍是 http/https（后端照着它决定
     连上游用 ws 还是 wss），外面这一层跟着门户自己的协议走 */
  function toProxyWs(input) {
    if (!input) return input;
    var raw = String(input), abs;
    try { abs = new NativeURL(raw, realHref()); } catch (e) { return input; }
    var up = abs.protocol === 'wss:' ? 'https' : abs.protocol === 'ws:' ? 'http' : null;
    if (!up) return input;
    if (abs.host === location.host && abs.pathname.indexOf(C.mount) === 0) return input;
    var host = abs.host, rest = abs.pathname + abs.search;
    if (abs.host === location.host) {
      var me;
      try { me = new NativeURL(realHref()); } catch (e) { return input; }
      host = me.host;
      up = me.protocol === 'https:' ? 'https' : 'http';
    } else if (!allowed(abs.hostname)) {
      return input;
    }
    return (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + mount(up, host, rest);
  }

  function toSrcset(value) {
    if (!value) return value;
    return String(value).split(',').map(function (part) {
      var piece = part.trim();
      if (!piece) return '';
      var bits = piece.split(/\s+/);
      bits[0] = toProxy(bits[0]);
      return bits.join(' ');
    }).filter(Boolean).join(', ');
  }

  window.__portal = { to: toProxy, nav: toNav, ws: toProxyWs, real: realHref, cfg: C, un: unproxy };

  /* ---------------- new URL(x, base) ---------------- */

  /* **改写把绝对地址变成了相对地址，而 `new URL` 的基准必须是绝对的。**

     app/proxyrewrite.py 把正文里的 `https://jp.mercari.com` 换成
     `/api/proxy/<id>/__portal__/https/jp.mercari.com`，是一条根绝对路径。
     站点代码里 `new URL(path, 'https://jp.mercari.com')` 这种写法非常常见，
     换完之后基准不再是绝对地址，构造器当场抛 TypeError。React 那类框架的
     错误边界一接住，整页就变成站点自己的「页面加载失败」——メルカリ 首页
     正是这么白的（`<html id="__next_error__">`），而且报错发生在浏览器里，
     后端日志一片 200，非常难查。

     基准解析得开就一个字都不动（绝大多数调用都走这条）。解不开才按
     「它本来是一条上游地址」还原：基准和输入都换回上游的真实地址，
     在上游那边解析完，再折回代理底下。

     **不能图省事拿门户的 origin 去补基准**：`new URL('/v1/x', 上游A)` 会变成
     「门户/v1/x」，上游 A 那个主机名就丢了，请求打到门户自己头上。 */
  function urlArgs(input, base) {
    /* 只认「被改写过的绝对地址」这一种形状（改写出来的一律以 C.mount 打头）。
       基准本来就是绝对地址的走不到这儿；`new URL(x, null)`、`new URL(x, '')`
       这类照旧抛——那是站点自己的毛病，不该被这里悄悄兜掉 */
    var b = String(base);
    if (b.indexOf(C.mount) !== 0) return null;

    var realBase;
    try { realBase = unproxy(new NativeURL(b, location.origin)); } catch (e) { return null; }
    if (!realBase) return null;

    /* 输入也可能被改写过（`new URL(绝对地址A, 绝对地址B)` 两个都会被换掉）。
       只还原确实指着代理的那些：相对地址得留给下面按 realBase 解析 */
    var raw = (input === null || input === undefined) ? '' : String(input), realIn = raw;
    try {
      if (raw.indexOf(C.mount) === 0) {
        realIn = unproxy(new NativeURL(raw, location.origin)) || raw;
      } else if (/^https?:/i.test(raw)) {
        var abs = new NativeURL(raw);
        if (abs.origin === location.origin) realIn = unproxy(abs) || raw;
      }
    } catch (e) {}

    var out;
    try { out = new NativeURL(realIn, realBase); } catch (e) { return null; }
    /* toProxy 回来的要么是代理路径，要么（白名单之外）是原样的上游绝对地址，
       两种都拿 location.href 当基准解析得开。给回代理路径而不是上游地址：
       这个站的 JS 里其余地址也都是改写过的，跟着一致，
       站点把结果丢给 `location.href` 时也还留在代理底下 */
    return [toProxy(out.href), location.href];
  }

  try {
    var PortalURL = function (input, base) {
      if (base === undefined) {
        /* 单参数的 `new URL(x)` 同样要求 x 是绝对地址。站点原本写的是一条完整地址，
           被改写成 `/api/proxy/<id>/…` 之后就不是了——补上门户自己的 origin，
           指向的还是同一个地方。不是代理路径的照旧抛，那是站点自己的毛病 */
        var one = (input === null || input === undefined) ? '' : String(input);
        return one.indexOf(C.mount) === 0
          ? new NativeURL(one, location.origin) : new NativeURL(input);
      }
      var fixed = urlArgs(input, base);
      return fixed ? new NativeURL(fixed[0], fixed[1]) : new NativeURL(input, base);
    };
    /* 原型要共用，不然页面里的 `x instanceof URL` 会变成 false；
       静态方法（createObjectURL 那几个）是不可枚举的，for-in 抄不过来，
       所以走原型链，再把常用的那几个显式绑一遍 */
    PortalURL.prototype = NativeURL.prototype;
    try { Object.setPrototypeOf(PortalURL, NativeURL); } catch (e) {}
    ['createObjectURL', 'revokeObjectURL'].forEach(function (k) {
      if (typeof NativeURL[k] === 'function') PortalURL[k] = NativeURL[k].bind(NativeURL);
    });
    /* canParse / parse 是同一个构造器的另外两张脸，基准的毛病一模一样。
       它们不抛异常，只会回 false / null，不修的话站点会以为那条地址不合法 */
    ['canParse', 'parse'].forEach(function (k) {
      if (typeof NativeURL[k] !== 'function') return;
      var orig = NativeURL[k];
      PortalURL[k] = function (a, b) {
        if (b === undefined) return orig.call(NativeURL, a);
        var fixed = urlArgs(a, b);
        return fixed ? orig.call(NativeURL, fixed[0], fixed[1]) : orig.call(NativeURL, a, b);
      };
    });
    window.URL = PortalURL;
    if (window.webkitURL === NativeURL) window.webkitURL = PortalURL;
  } catch (e) {}

  function wrap(owner, name, make) {
    try {
      var orig = owner[name];
      if (typeof orig !== 'function') return;
      owner[name] = make(orig);
    } catch (e) { /* 拦不住就算了，页面照常跑，大不了那一条请求没走代理 */ }
  }

  /* ---------------- 取数据的几个出口 ---------------- */

  wrap(window, 'fetch', function (orig) {
    return function (input, init) {
      try {
        if (typeof input === 'string' || input instanceof URL) {
          input = toProxy(String(input));
        } else if (input && typeof input === 'object' && input.url) {
          var next = toProxy(input.url);
          /* new Request(新地址, 老请求) 会把方法、首部、请求体一并带过来 */
          if (next !== input.url) input = new Request(next, input);
        }
      } catch (e) { /* 换不成就按原样发，总比整条请求发不出去强 */ }
      /* 不用 this：经 routerView 那层 Proxy 调进来时 this 是 Proxy，原生 fetch 不认 */
      return orig.call(window, input, init);
    };
  });

  wrap(XMLHttpRequest.prototype, 'open', function (orig) {
    return function (method, url) {
      var args = Array.prototype.slice.call(arguments);
      try { args[1] = toProxy(url); } catch (e) {}
      return orig.apply(this, args);
    };
  });

  wrap(navigator, 'sendBeacon', function (orig) {
    return function (url, data) { return orig.call(navigator, toProxy(url), data); };
  });

  /* 构造函数这几个要连着原型和静态常量一起搬过去，不然 instanceof 和
     WebSocket.OPEN 这类写法会当场报错 */
  function wrapCtor(name, conv) {
    try {
      var Orig = window[name];
      if (typeof Orig !== 'function') return;
      var Next = function (url, extra) {
        try { url = conv(url); } catch (e) {}
        return extra === undefined ? new Orig(url) : new Orig(url, extra);
      };
      Next.prototype = Orig.prototype;
      for (var k in Orig) { try { Next[k] = Orig[k]; } catch (e) {} }
      ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'].forEach(function (k) {
        if (k in Orig) { try { Next[k] = Orig[k]; } catch (e) {} }
      });
      window[name] = Next;
    } catch (e) {}
  }
  wrapCtor('WebSocket', toProxyWs);
  wrapCtor('EventSource', toProxy);
  wrapCtor('Worker', toProxy);
  wrapCtor('SharedWorker', toProxy);

  wrap(window, 'open', function (orig) {
    return function () {
      var args = Array.prototype.slice.call(arguments);
      if (args[0]) { try { args[0] = toNav(args[0]); } catch (e) {} }
      return orig.apply(window, args);
    };
  });

  /* ---------------- 元素上的地址属性 ---------------- */

  function patchProp(ctor, prop, conv) {
    try {
      var Ctor = window[ctor];
      if (!Ctor || !Ctor.prototype) return;
      var d = Object.getOwnPropertyDescriptor(Ctor.prototype, prop);
      if (!d || !d.set || !d.configurable) return;
      Object.defineProperty(Ctor.prototype, prop, {
        configurable: true,
        enumerable: d.enumerable,
        get: d.get,
        set: function (v) {
          try { v = conv.call(this, v); } catch (e) {}
          d.set.call(this, v);
        }
      });
    } catch (e) {}
  }

  /* 会换页的那几种标签：它们身上的地址按「点过去的页面」办（toNav）——
     **只限已经挂进文档的**。没挂进去的 <a> 多半是拿来解析地址的：
     `a = createElement('a'); a.href = url; 读 a.hostname / a.href`，gtag 这类统计广告脚本都这么写。
     按 toNav 折进代理的话它读回来的就是门户地址，接着拿这条去发请求，第三方统计广告
     就全绕到门户上来了（门户那台机器的 DNS 拦广告时就是一串 502）。
     没挂进去的按 toProxy 办（只认白名单）；挂进文档时 MutationObserver 会再按 toNav 过一遍，
     没挂进去就直接 click() 的由下面 fixTarget 那段兜 */
  function navIfAttached(v) { return this && this.isConnected ? toNav(v) : toProxy(v); }

  [['HTMLScriptElement', 'src'], ['HTMLImageElement', 'src'], ['HTMLLinkElement', 'href'],
   ['HTMLIFrameElement', 'src'], ['HTMLSourceElement', 'src'], ['HTMLMediaElement', 'src'],
   ['HTMLVideoElement', 'poster'], ['HTMLEmbedElement', 'src'], ['HTMLObjectElement', 'data'],
   ['HTMLTrackElement', 'src']].forEach(function (p) { patchProp(p[0], p[1], toProxy); });
  [['HTMLAnchorElement', 'href'], ['HTMLAreaElement', 'href'],
   ['HTMLFormElement', 'action'], ['HTMLInputElement', 'formAction'],
   ['HTMLButtonElement', 'formAction']].forEach(function (p) { patchProp(p[0], p[1], navIfAttached); });

  var NAVTAGS = { A: 1, AREA: 1, FORM: 1, BUTTON: 1, INPUT: 1 };
  function convFor(el, attr) {
    return el && NAVTAGS[el.tagName] && el.isConnected
      && (attr === 'href' || attr === 'action' || attr === 'formaction')
      ? toNav : toProxy;
  }
  [['HTMLImageElement', 'srcset'], ['HTMLSourceElement', 'srcset']]
    .forEach(function (p) { patchProp(p[0], p[1], toSrcset); });

  wrap(Element.prototype, 'setAttribute', function (orig) {
    return function (name, value) {
      try {
        var k = String(name).toLowerCase();
        if (SETATTRS[k]) value = convFor(this, k)(value);
        else if (k === 'srcset' || k === 'imagesrcset') value = toSrcset(value);
      } catch (e) {}
      return orig.call(this, name, value);
    };
  });

  wrap(Element.prototype, 'setAttributeNS', function (orig) {
    return function (ns, name, value) {
      try {
        var k = String(name).toLowerCase().replace(/^.*:/, '');
        if (SETATTRS[k]) value = toProxy(value);
      } catch (e) {}
      return orig.call(this, ns, name, value);
    };
  });

  /* innerHTML / outerHTML 塞进来的节点不经过上面两条，扫一遍补上 */
  function fixNode(el) {
    if (!el || el.nodeType !== 1 || !el.getAttribute) return;
    for (var i = 0; i < ATTRS.length; i++) {
      var a = ATTRS[i];
      if (!el.hasAttribute(a)) continue;
      var v = el.getAttribute(a), n = convFor(el, a)(v);
      if (n !== v) el.setAttribute(a, n);
    }
    if (el.hasAttribute('srcset')) {
      var s = el.getAttribute('srcset'), t = toSrcset(s);
      if (t !== s) el.setAttribute('srcset', t);
    }
  }

  try {
    var SEL = '[src],[href],[srcset],[action],[data],[poster]';
    new MutationObserver(function (records) {
      for (var i = 0; i < records.length; i++) {
        var added = records[i].addedNodes;
        for (var j = 0; j < added.length; j++) {
          var el = added[j];
          if (!el || el.nodeType !== 1) continue;
          fixNode(el);
          if (el.firstElementChild && el.querySelectorAll) {
            var kids = el.querySelectorAll(SEL);
            for (var k = 0; k < kids.length; k++) fixNode(kids[k]);
          }
        }
      }
    }).observe(document.documentElement, { childList: true, subtree: true });
  } catch (e) {}

  /* 点下去那一刻再兜一遍：链接的地址有的是框架直接改 DOM 属性以外的路子写进去的
     （比如先建好节点、挂进文档之后才补 href，那一下 MutationObserver 看不见）。
     捕获阶段、在站点自己的点击处理之前换好，浏览器随后照着换过的地址去 */
  function fixTarget(el) {
    for (; el && el.nodeType === 1; el = el.parentNode) {
      if (el.tagName === 'A' || el.tagName === 'AREA') {
        var h = el.getAttribute('href');
        if (h) { var n = toNav(h); if (n !== h) el.setAttribute('href', n); }
        return;
      }
    }
  }
  /* 没挂进文档的 <a> 直接 click()（下载、跳新页常这么写）：点击事件传不到 document，
     上面那个捕获阶段的监听收不到，在这儿补那一下 */
  wrap(HTMLAnchorElement.prototype, 'click', function (orig) {
    return function () {
      try { if (!this.isConnected) fixTarget(this); } catch (e) {}
      return orig.apply(this, arguments);
    };
  });

  try {
    document.addEventListener('click', function (ev) { try { fixTarget(ev.target); } catch (e) {} }, true);
    document.addEventListener('auxclick', function (ev) { try { fixTarget(ev.target); } catch (e) {} }, true);
    document.addEventListener('submit', function (ev) {
      try {
        var f = ev.target, a = f && f.getAttribute && f.getAttribute('action');
        if (a) { var n = toNav(a); if (n !== a) f.setAttribute('action', n); }
      } catch (e) {}
    }, true);
  } catch (e) {}

  /* ---------------- 地址栏与 Cookie ---------------- */

  ['pushState', 'replaceState'].forEach(function (name) {
    wrap(history, name, function (orig) {
      return function (state, title, url) {
        if (url !== undefined && url !== null) { try { url = toProxy(url); } catch (e) {} }
        return orig.call(history, state, title, url);
      };
    });
  });

  /* `location.href = x`、`location.assign(x)`、`location.replace(x)` 是 unforgeable 的，包不了；
     但 Navigation API 的 navigate 事件在它们真跳之前先发一次，push / replace 这两种还能取消。
     所以在这儿拦：目的地出了门户这个源的，取消掉，换成 toNav 之后的地址重新跳一次。

     メルカリ 就栽在这儿：点「ログイン」是 `window.location.assign('https://auth.mercari.com/…')`，
     而它关着 JS 改写（REWRITE_JS = False），那条字面量没被换。浏览器直连 auth.mercari.com，
     带的是浏览器自己那份 Cookie——本机登着的话转一圈回来，已经是真 jp.mercari.com 上登好的页面，
     整个出了代理，Cookie 代理那个罐子也就白开了。

     只管跨源的：门户这个源上的（根绝对地址被解析到门户头上那种，`/api/oidc/` 也在这儿）
     照旧交给 app/proxy.py 的 Fallback。带请求体的（POST 表单）不碰——取消了重新跳，
     请求体就丢了，那种靠上面 submit 那段在提交前换好 action。下载、同文档内的跳转也不碰。
     重新跳放进 setTimeout：不在 navigate 事件里套一次新的跳转，各家实现对那种重入的处理不一定齐。
     浏览器没有 Navigation API（老版 Safari / Firefox）时这一段整个跳过，和以前一样拦不住。 */
  try {
    if (window.navigation && typeof window.navigation.addEventListener === 'function') {
      window.navigation.addEventListener('navigate', function (ev) {
        try {
          if (!ev.cancelable || ev.formData || ev.downloadRequest != null) return;
          if (ev.navigationType !== 'push' && ev.navigationType !== 'replace') return;
          var dest = ev.destination;
          if (!dest || dest.sameDocument || !dest.url) return;
          if (new NativeURL(dest.url).origin === location.origin) return;
          var next = toNav(dest.url);
          if (next === dest.url) return;    /* 非 http(s)、带 user@ 的：转不了，放它走 */
          ev.preventDefault();
          var replace = ev.navigationType === 'replace';
          setTimeout(function () { replace ? location.replace(next) : location.assign(next); }, 0);
        } catch (e) {}
      });
    }
  } catch (e) {}

  /* ---------------- 前端路由看到的地址（站点模块按需打开） ----------------
     页面在浏览器里的路径是 /api/proxy/<id>/__portal__/https/<主机>/signin，前端路由拿它去匹配，
     一条都对不上，渲染出来的是站点自己的「页面不存在」——login.jp.mercari.com 就是这样。
     `location` 是 unforgeable 的，换不掉；但 React Router（v6 / @remix-run/router，老的 history 库同理）
     的 BrowserRouter 不读全局的 location，读的是 `document.defaultView.location`。
     所以把 `document.defaultView` 换成 window 外面的一层 Proxy：别的照原样转给 window，
     只有 `location` 给一个按上游地址算的（pathname 是 /signin）。改地址栏照旧走 history.pushState，
     上面那层包装会把它折回代理底下。

     **默认不开**，由站点模块在 BROWSER_JS 里调 `window.__portal.routerView()`：开了之后
     defaultView 不再 === window，拿它和 window、event.source 比相等的代码会对不上；
     读全局 location 的路由（Vue Router、Next.js）这招也帮不上。 */
  function routerView() {
    if (routerView.on) return;
    routerView.on = true;
    try {
      var dv = Object.getOwnPropertyDescriptor(Document.prototype, 'defaultView');
      if (!dv || !dv.get || !dv.configurable) return;
      var now = function () { return new NativeURL(realHref()); };
      var loc = {
        assign: function (u) { location.assign(toNav(u)); },
        replace: function (u) { location.replace(toNav(u)); },
        reload: function () { location.reload(); },
        toString: function () { return realHref(); }
      };
      ['href', 'origin', 'protocol', 'host', 'hostname', 'port', 'pathname', 'search', 'hash']
        .forEach(function (k) {
          Object.defineProperty(loc, k, {
            enumerable: true,
            get: function () { return now()[k]; },
            set: function (v) { var u = now(); u[k] = v; location.assign(toNav(u.href)); }
          });
        });
      /* window 上的函数要绑回 window 再交出去：经 Proxy 调的话 this 是 Proxy，
         原生方法当场抛 Illegal invocation。构造函数不绑：绑过的函数没有 prototype，
         `view.HTMLElement.prototype` 会变成 undefined。
         **按属性名分，不按函数长相分**：window 上大写开头的是构造函数 / 接口（HTMLElement、URL、
         Image），方法都是小写开头。看函数本身（原生的？有没有 prototype？）靠不住——
         Sentry 拿普通 function 把 addEventListener 换掉，还给它安了个 prototype，
         里面再 apply(this)，不绑就炸（登录页实测，整页白屏） */
      var bound = new WeakMap();
      var view = new Proxy(window, {
        get: function (t, k) {
          if (k === 'location') return loc;
          var v = Reflect.get(t, k);
          if (v === t) return view;               /* window.window、self 这类指回自己的 */
          if (typeof v !== 'function' || typeof k === 'string' && /^[A-Z]/.test(k)) return v;
          var b = bound.get(v);
          if (!b) { b = v.bind(t); bound.set(v, b); }
          return b;
        },
        set: function (t, k, v) { t[k] = v; return true; }
      });
      Object.defineProperty(Document.prototype, 'defaultView', {
        configurable: true,
        enumerable: dv.enumerable,
        get: function () { var w = dv.get.call(this); return w === window ? view : w; }
      });
    } catch (e) {}
  }
  window.__portal.routerView = routerView;

  /* 脚本自己种的 Cookie 也得钉在这张卡片的路径下，否则 path=/ 会让它跟着门户
     每个请求跑，几个站点的同名 Cookie 互相顶掉 */
  try {
    var cd = Object.getOwnPropertyDescriptor(Document.prototype, 'cookie');
    if (cd && cd.configurable && cd.set) {
      Object.defineProperty(Document.prototype, 'cookie', {
        configurable: true,
        enumerable: cd.enumerable,
        get: function () { return cd.get.call(this); },
        set: function (v) {
          try {
            var s = String(v).replace(/;\s*domain\s*=[^;]*/ig, '').replace(/;\s*path\s*=[^;]*/ig, '');
            if (location.protocol !== 'https:') {
              s = s.replace(/;\s*secure\b/ig, '').replace(/;\s*samesite\s*=\s*none/ig, '; SameSite=Lax');
            }
            v = s + '; path=' + C.mount;
          } catch (e) {}
          cd.set.call(this, v);
        }
      });
    }
  } catch (e) {}

  /* document.domain = 'xxx' 在门户这个域名下会抛 SecurityError，把整个脚本带停。
     代理底下本来就都是同源，这个赋值没有意义，吞掉 */
  try {
    var dd = Object.getOwnPropertyDescriptor(Document.prototype, 'domain');
    if (dd && dd.configurable) {
      Object.defineProperty(Document.prototype, 'domain', {
        configurable: true, enumerable: dd.enumerable, get: dd.get, set: function () {}
      });
    }
  } catch (e) {}

  /* ---------------- 本地存储：按「门户用户 + 卡片」分开（见模块头） ----------------
     window 上那两个属性换成一个 Proxy：键前面加上 C.store 再去读写原生那份，
     列键、length、clear() 都只算自己这个前缀底下的。原生那份里不带前缀的老数据
     （改成这样之前存的）从此看不见，也不去动它——分不清是谁的 */
  function scopedStorage(native, prefix) {
    function keys() {
      var out = [];
      for (var i = 0; i < native.length; i++) {
        var k = native.key(i);
        if (k !== null && k.indexOf(prefix) === 0) out.push(k.slice(prefix.length));
      }
      return out;
    }
    function method(fn) { return { value: fn, writable: true, configurable: true }; }
    /* 原型挂 Storage.prototype：`localStorage instanceof Storage` 照样成立。
       方法都是 configurable 的，下面 ownKeys 只报存的那些键才不违反 Proxy 的约束 */
    var api = Object.create(Storage.prototype, {
      getItem: method(function (k) { return native.getItem(prefix + k); }),
      setItem: method(function (k, v) { native.setItem(prefix + k, v); }),
      removeItem: method(function (k) { native.removeItem(prefix + k); }),
      clear: method(function () { keys().forEach(function (k) { native.removeItem(prefix + k); }); }),
      key: method(function (i) { var ks = keys(); return ks[i] === undefined ? null : ks[i]; })
    });
    return new Proxy(api, {
      get: function (t, k) {
        if (k === 'length') return keys().length;
        if (typeof k === 'symbol' || k in t) return t[k];
        var v = native.getItem(prefix + k);          /* localStorage.foo 这种写法 */
        return v === null ? undefined : v;
      },
      set: function (t, k, v) {
        if (typeof k === 'symbol') t[k] = v;
        else native.setItem(prefix + k, v);
        return true;
      },
      has: function (t, k) {
        return typeof k === 'symbol' || k in t ? k in t : native.getItem(prefix + k) !== null;
      },
      deleteProperty: function (t, k) {
        if (typeof k !== 'symbol') native.removeItem(prefix + k);
        return true;
      },
      ownKeys: function () { return keys(); },
      getOwnPropertyDescriptor: function (t, k) {
        if (typeof k === 'symbol') return undefined;
        var v = native.getItem(prefix + k);
        return v === null ? undefined : { value: v, writable: true, enumerable: true, configurable: true };
      },
      defineProperty: function (t, k, d) {
        if (typeof k !== 'symbol' && 'value' in d) native.setItem(prefix + k, d.value);
        return true;
      }
    });
  }
  if (C.store) {
    ['localStorage', 'sessionStorage'].forEach(function (name) {
      try {
        /* Chrome / Firefox 里它们是 window 自己的访问器属性，可重定义 */
        var owner = Object.prototype.hasOwnProperty.call(window, name) ? window : Window.prototype;
        var d = Object.getOwnPropertyDescriptor(owner, name);
        if (!d || !d.get || !d.configurable) return;
        var native = d.get.call(window);              /* 存储被禁用时这一句会抛，那就不动它 */
        if (!native) return;
        var view = scopedStorage(native, C.store);
        Object.defineProperty(window, name, {
          configurable: true, enumerable: d.enumerable, get: function () { return view; }
        });
      } catch (e) {}
    });
  }

  /* Service Worker 会横在所有请求前面按它自己那套改地址，和这里打架；
     而且装上之后要进浏览器设置才卸得掉。直接让注册失败 */
  try {
    if (navigator.serviceWorker) {
      Object.defineProperty(navigator.serviceWorker, 'register', {
        configurable: true, writable: true,
        value: function () { return Promise.reject(new Error('门户代理下不启用 Service Worker')); }
      });
    }
  } catch (e) {}

  __PORTAL_SITE_JS__
})();
"""


def script(base: str, mount: str, scheme: str, host: str,
           allow: tuple[str, ...], extra_js: str = '', *, store: str = '') -> str:
    """拼出要插进 `<head>` 的那个 `<script>`。`store` 是本地存储的键前缀，空串 = 不分开。

    配置是 `json.dumps` 出来的，所以卡片名里有引号、主机名里有奇怪字符都不会把
    脚本拼断。`</` 要转义掉：正文里出现 `</script` 的话 HTML 解析器会就地把脚本
    截断，后半截当成页面内容显示出来。
    """
    config = json.dumps({
        'base': base,        # /api/proxy/<id>/__portal__/
        'mount': mount,      # /api/proxy/<id>/
        'scheme': scheme,    # 当前页面在上游那边的协议
        'host': host,        # 当前页面在上游那边的主机名
        'allow': list(allow),
        'store': store,      # localStorage / sessionStorage 的键前缀，见模块头
    }, ensure_ascii=False)
    body = (_JS
            .replace('__PORTAL_CONFIG__', config)
            .replace('__PORTAL_SITE_JS__', extra_js or '')
            .replace('</', '<\\/'))
    return '<script>' + body + '</script>'
