#!/usr/bin/env python
"""命令行管理：建用户、改密码、注册业务系统。

    python manage.py init
    python manage.py adduser zhangsan --name 张三
    python manage.py passwd zhangsan
    python manage.py rename zhangsan zhang.san
    python manage.py users
    python manage.py addclient crm --name 客户管理 --redirect-uri http://192.168.1.20:8080/sso/callback
    python manage.py clients
    python manage.py settings
    python manage.py set cookie_secure true
"""
import argparse
import getpass
import sys

from app import clients, db, settings, winconsole
from app.security import new_token


def _ask_password(prompt: str = '密码: ') -> str:
    # 打包成 exe 之后这里是借来的控制台，提示符已经还给 cmd 了，再 getpass
    # 就是和人抢输入缓冲，敲进去的字符两边各拿一半。改弹个小框收，原因见
    # app/winconsole.py 开头。源码态走不到这一支。
    if not winconsole.can_prompt():
        pw = winconsole.ask_password(hint=prompt.rstrip(': ').strip() or '设置口令')
        if pw is None:
            raise SystemExit('已取消。')
        return pw
    while True:
        pw = getpass.getpass(prompt)
        if len(pw) < 6:
            print('至少 6 位，重来。')
            continue
        if pw != getpass.getpass('再输一次: '):
            print('两次不一致，重来。')
            continue
        return pw


def _warn_default_password() -> None:
    """默认口令是公开可猜的，只要没改就一直提醒，别让它悄悄留在生产上。"""
    if not db.uses_default_password():
        return
    print()
    print('  !! 账号 admin 还在用默认口令 admin。')
    print('     谁能打开这个门户，谁就能免登录进所有已接入的业务系统。')
    print(f'     改掉：python manage.py passwd {db.DEFAULT_ADMIN}')
    print()


def cmd_init(args) -> int:
    db.init_db()
    print(f'数据库就绪: {db.describe()}')
    print('已建表: users / sessions / tickets / session_clients / settings / nav / clients')
    if not db.list_users():
        db.create_user(db.DEFAULT_ADMIN, db.DEFAULT_PASSWORD,
                       display_name='管理员', roles='admin')
        print(f'\n已创建默认管理员：{db.DEFAULT_ADMIN} / {db.DEFAULT_PASSWORD}')
    _warn_default_password()
    return 0


def cmd_adduser(args) -> int:
    password = args.password or _ask_password()
    try:
        user = db.create_user(args.username, password, args.name or args.username,
                              args.email or '', args.roles or '')
    except ValueError as exc:          # 用户名格式不合法，db.validate_username 挡下来的
        print(exc, file=sys.stderr)
        return 1
    except db.IntegrityError:
        print(f'用户 {args.username} 已存在', file=sys.stderr)
        return 1
    print(f"已创建 {user['username']}（{user['display_name']}）")
    return 0


def cmd_rename(args) -> int:
    if db.get_user(args.username) is None:
        print(f'没有用户 {args.username}', file=sys.stderr)
        return 1
    try:
        user = db.update_user(args.username, new_username=args.new_username)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    except db.IntegrityError:
        print(f'用户名 {args.new_username} 已经有人用了', file=sys.stderr)
        return 1
    print(f"{args.username} 已改名为 {user['username']}（在线会话不受影响）")
    print('  注意：按用户名认人的业务系统那边会当成另一个人，记得同步改。')
    return 0


def cmd_passwd(args) -> int:
    if db.get_user(args.username) is None:
        print(f'没有用户 {args.username}', file=sys.stderr)
        return 1
    db.set_password(args.username, args.password or _ask_password('新密码: '))
    print(f'{args.username} 的密码已更新')
    return 0


def cmd_users(args) -> int:
    rows = db.list_users()
    if not rows:
        print('（还没有用户，跑 python manage.py adduser <用户名>）')
        return 0
    width = max(len(r['username']) for r in rows)
    for r in rows:
        flag = '停用' if r['disabled'] else '正常'
        roles = ','.join(r['roles']) or '-'
        print(f"{r['username']:<{width}}  {flag}  {r['display_name']}  角色:{roles}")
    _warn_default_password()
    return 0


def cmd_toggle(args) -> int:
    disabled = args.cmd == 'disable'
    if not db.set_disabled(args.username, disabled):
        print(f'没有用户 {args.username}', file=sys.stderr)
        return 1
    print(f"{args.username} 已{'停用（在线会话一并踢掉）' if disabled else '启用'}")
    return 0


def cmd_deluser(args) -> int:
    if not db.delete_user(args.username):
        print(f'没有用户 {args.username}', file=sys.stderr)
        return 1
    print(f'{args.username} 已删除')
    return 0


def cmd_addclient(args) -> int:
    if clients.exists(args.client_id) and not args.force:
        print(f'{args.client_id} 已存在，加 --force 覆盖', file=sys.stderr)
        return 1
    client = clients.upsert(
        args.client_id,
        name=args.name or args.client_id,
        secret=args.secret or new_token(),
        redirect_uris=args.redirect_uri,
        logout_uri=args.logout_uri or '',
        home_url=args.home_url or '',
    )
    print(f'已注册 {args.client_id}')
    print(f'  client_id     = {args.client_id}')
    print(f'  client_secret = {client["secret"]}')
    print('  把这两个值配到业务系统里，secret 只在服务端之间用，别写进前端。')
    return 0


def cmd_clients(args) -> int:
    registry = clients.load(force=True)
    if not registry:
        print('（还没注册业务系统，跑 python manage.py addclient <id> --redirect-uri <地址>）')
        return 0
    for cid, cfg in registry.items():
        secret = cfg['secret'] if args.show_secret else (cfg['secret'][:6] + '…' if cfg['secret'] else '(未设置)')
        print(f"{cid}  {cfg['name']}")
        print(f'  secret       : {secret}')
        print(f"  redirect_uris: {', '.join(cfg['redirect_uris']) or '(空，无法跳转)'}")
        if cfg['logout_uri']:
            print(f"  logout_uri   : {cfg['logout_uri']}")
    return 0


def cmd_delclient(args) -> int:
    if not clients.delete(args.client_id):
        print(f'没有 {args.client_id}', file=sys.stderr)
        return 1
    print(f'{args.client_id} 已删除')
    return 0


def cmd_settings(args) -> int:
    rows = settings.all_items()
    width = max(len(r['name']) for r in rows)
    for r in rows:
        mark = '*' if r['changed'] else ' '
        limit = f"，{r['low']}~{r['high']}" if r['kind'] == 'int' else ''
        print(f"{mark} {r['name']:<{width}}  {r['value']}")
        print(f"  {'':<{width}}  {r['note']}（默认 {r['default']}{limit}）")
        if r['caution']:
            print(f"  {'':<{width}}  注意：{r['caution']}")
    print()
    print('* = 已改过默认值。改：python manage.py set <名字> <值>')
    return 0


def cmd_set(args) -> int:
    try:
        value = settings.put(args.name, args.value)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        print('可选: ' + ', '.join(settings.DEFAULTS), file=sys.stderr)
        return 1
    spec = settings.DEFAULTS[args.name]
    if spec.caution:
        print(f'注意：{spec.caution}')
    print(f'{args.name} = {value}（最多 5 秒后生效，不用重启）')
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='门户单点登录管理工具')
    sub = parser.add_subparsers(dest='cmd', required=True)

    sub.add_parser('init', help='初始化数据库和配置，并建第一个用户').set_defaults(func=cmd_init)

    p = sub.add_parser('adduser', help='新增用户')
    p.add_argument('username')
    p.add_argument('--name', help='显示名')
    p.add_argument('--email')
    p.add_argument('--roles', help='逗号分隔，会原样传给业务系统')
    p.add_argument('--password', help='不写就交互输入（推荐，免得留在命令历史里）')
    p.set_defaults(func=cmd_adduser)

    p = sub.add_parser('passwd', help='改密码')
    p.add_argument('username')
    p.add_argument('--password')
    p.set_defaults(func=cmd_passwd)

    sub.add_parser('users', help='列出用户').set_defaults(func=cmd_users)

    p = sub.add_parser('rename', help='改用户名（会话不受影响，业务系统那边要同步改）')
    p.add_argument('username')
    p.add_argument('new_username')
    p.set_defaults(func=cmd_rename)

    for name, helptext in (('disable', '停用用户'), ('enable', '启用用户')):
        p = sub.add_parser(name, help=helptext)
        p.add_argument('username')
        p.set_defaults(func=cmd_toggle)

    p = sub.add_parser('deluser', help='删除用户')
    p.add_argument('username')
    p.set_defaults(func=cmd_deluser)

    p = sub.add_parser('addclient', help='注册一个业务系统')
    p.add_argument('client_id')
    p.add_argument('--name', help='显示名，会出现在门户的下拉框里')
    p.add_argument('--redirect-uri', action='append', default=[], required=True,
                   help='回调地址，可重复；必须和业务系统实际用的一字不差')
    p.add_argument('--logout-uri', help='单点登出通知地址，可不填')
    p.add_argument('--home-url', help='系统首页，登出后回跳用')
    p.add_argument('--secret', help='不写就随机生成')
    p.add_argument('--force', action='store_true', help='覆盖同名配置')
    p.set_defaults(func=cmd_addclient)

    p = sub.add_parser('clients', help='列出业务系统')
    p.add_argument('--show-secret', action='store_true')
    p.set_defaults(func=cmd_clients)

    p = sub.add_parser('delclient', help='删除业务系统')
    p.add_argument('client_id')
    p.set_defaults(func=cmd_delclient)

    sub.add_parser('settings', help='列出运行期配置（存在数据库里）').set_defaults(func=cmd_settings)

    p = sub.add_parser('set', help='改一项运行期配置')
    p.add_argument('name')
    p.add_argument('value')
    p.set_defaults(func=cmd_set)

    return parser


def run(argv: list[str] | None = None) -> int:
    """命令行入口。打包后的 exe 也走这里（见 backend/main.py），
    逻辑别写回下面的 __main__ 里，不然两条路会慢慢跑偏。"""
    parsed = build_parser().parse_args(argv)
    try:
        db.init_db()
    except db.DatabaseUnavailable as exc:
        sys.exit(f'[数据库] {exc}')
    return parsed.func(parsed) or 0


if __name__ == '__main__':
    sys.exit(run())
