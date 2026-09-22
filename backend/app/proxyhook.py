"""注入被代理页面的那段脚本：运行时拼出来的地址，在真要发请求的那一刻折回门户。

app/proxyrewrite.py 改的是**正文里写死的地址**，覆盖不到这几种：

    fetch(location.origin + '/v1/items')      // 拿 origin 现拼的，字面量里没有主机名
    new Image().src = path                    // path 是接口回来的根绝对地址
    el.innerHTML = '<img src="https://...">'  // 整段 HTML 是运行时才有的

所以再补一道：把 `fetch` / `XMLHttpRequest` / `WebSocket` / `Worker` 这些出口，
以及元素的 `src` / `href` 这些属性全包一层，地址过一遍 `toProxy` 再放行。
白名单之外的地址**原样放行**（照旧直连），和后端那道是同一条规矩。

## 有意做成这样

- **整段内联进 HTML，不做成外链脚本**：它必须比页面里任何一个脚本先跑完，
  外链多一次往返不说，`async`/`defer` 的次序也不好保证。几 KB 而已。
- **拦不住的就是拦不住**：`location.href = '...'`、`location.replace(...)` 是
  unforgeable 属性，脚本改不了。这类跳转靠正文改写覆盖（字面量那半截已经被换掉了），
  再兜不住就落到门户根路径上，由 app/proxy.py 末尾那条 Referer 兜底转回来。
- **Service Worker 直接掐掉**：它注册下来的作用域是 `/api/proxy/<id>/…`，
  会横在所有请求前面按自己那套改地址，和这里两套改法打架；而且卸载它得进
  浏览器设置，坏起来非常难查。
- **`document.domain` 赋值吞掉**：雅虎那几个站的老脚本会写
  `document.domain = 'yahoo.co.jp'`，在门户这个域名下赋值直接抛 SecurityError，
  整个脚本就断在那儿了。
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

  function toProxy(input) {
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
    if (!allowed(abs.hostname)) return input;
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

  window.__portal = { to: toProxy, ws: toProxyWs, real: realHref, cfg: C, un: unproxy };

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
      return orig.call(this, input, init);
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
      if (args[0]) { try { args[0] = toProxy(args[0]); } catch (e) {} }
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
          try { v = conv(v); } catch (e) {}
          d.set.call(this, v);
        }
      });
    } catch (e) {}
  }

  [['HTMLScriptElement', 'src'], ['HTMLImageElement', 'src'], ['HTMLLinkElement', 'href'],
   ['HTMLIFrameElement', 'src'], ['HTMLSourceElement', 'src'], ['HTMLMediaElement', 'src'],
   ['HTMLVideoElement', 'poster'], ['HTMLEmbedElement', 'src'], ['HTMLObjectElement', 'data'],
   ['HTMLTrackElement', 'src'], ['HTMLAnchorElement', 'href'], ['HTMLAreaElement', 'href'],
   ['HTMLFormElement', 'action'], ['HTMLInputElement', 'formAction'],
   ['HTMLButtonElement', 'formAction']].forEach(function (p) { patchProp(p[0], p[1], toProxy); });
  [['HTMLImageElement', 'srcset'], ['HTMLSourceElement', 'srcset']]
    .forEach(function (p) { patchProp(p[0], p[1], toSrcset); });

  wrap(Element.prototype, 'setAttribute', function (orig) {
    return function (name, value) {
      try {
        var k = String(name).toLowerCase();
        if (SETATTRS[k]) value = toProxy(value);
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
      var v = el.getAttribute(a), n = toProxy(v);
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

  /* ---------------- 地址栏与 Cookie ---------------- */

  ['pushState', 'replaceState'].forEach(function (name) {
    wrap(history, name, function (orig) {
      return function (state, title, url) {
        if (url !== undefined && url !== null) { try { url = toProxy(url); } catch (e) {} }
        return orig.call(history, state, title, url);
      };
    });
  });

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
           allow: tuple[str, ...], extra_js: str = '') -> str:
    """拼出要插进 `<head>` 的那个 `<script>`。

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
    }, ensure_ascii=False)
    body = (_JS
            .replace('__PORTAL_CONFIG__', config)
            .replace('__PORTAL_SITE_JS__', extra_js or '')
            .replace('</', '<\\/'))
    return '<script>' + body + '</script>'
