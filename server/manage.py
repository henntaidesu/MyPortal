#!/usr/bin/env python
"""命令行管理：建用户、改密码、注册业务系统。

    python manage.py init
    python manage.py adduser zhangsan --name 张三
    python manage.py passwd zhangsan
    python manage.py users
    python manage.py addclient crm --name 客户管理 --redirect-uri http://192.168.1.20:8080/sso/callback
    python manage.py clients
"""
import argparse
import getpass
import sqlite3
import sys

from app import clients, db
from app.security import new_token


def _ask_password(prompt: str = '密码: ') -> str:
    while True:
        pw = getpass.getpass(prompt)
        if len(pw) < 6:
            print('至少 6 位，重来。')
            continue
        if pw != getpass.getpass('再输一次: '):
            print('两次不一致，重来。')
            continue
        return pw


def cmd_init(args) -> int:
    db.init_db()
    clients.write_template()
    print(f'数据库就绪: {db.DB_PATH}')
    print(f'业务系统配置: {clients.CLIENTS_PATH}')
    if not db.list_users():
        print('\n还没有任何用户，现在建第一个：')
        username = input('用户名: ').strip() or 'admin'
        db.create_user(username, _ask_password(), display_name=username, roles='admin')
        print(f'已创建 {username}')
    return 0


def cmd_adduser(args) -> int:
    password = args.password or _ask_password()
    try:
        user = db.create_user(args.username, password, args.name or args.username,
                              args.email or '', args.roles or '')
    except sqlite3.IntegrityError:
        print(f'用户 {args.username} 已存在', file=sys.stderr)
        return 1
    print(f"已创建 {user['username']}（{user['display_name']}）")
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
    registry = dict(clients.load(force=True))
    if args.client_id in registry and not args.force:
        print(f'{args.client_id} 已存在，加 --force 覆盖', file=sys.stderr)
        return 1
    secret = args.secret or new_token()
    registry[args.client_id] = {
        'name': args.name or args.client_id,
        'secret': secret,
        'redirect_uris': args.redirect_uri,
        'logout_uri': args.logout_uri or '',
        'home_url': args.home_url or '',
    }
    clients.save(registry)
    print(f'已注册 {args.client_id}')
    print(f'  client_id     = {args.client_id}')
    print(f'  client_secret = {secret}')
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
    registry = dict(clients.load(force=True))
    if registry.pop(args.client_id, None) is None:
        print(f'没有 {args.client_id}', file=sys.stderr)
        return 1
    clients.save(registry)
    print(f'{args.client_id} 已删除')
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='主页单点登录管理工具')
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

    for name, helptext in (('disable', '停用用户'), ('enable', '启用用户')):
        p = sub.add_parser(name, help=helptext)
        p.add_argument('username')
        p.set_defaults(func=cmd_toggle)

    p = sub.add_parser('deluser', help='删除用户')
    p.add_argument('username')
    p.set_defaults(func=cmd_deluser)

    p = sub.add_parser('addclient', help='注册一个业务系统')
    p.add_argument('client_id')
    p.add_argument('--name', help='显示名，会出现在主页的下拉框里')
    p.add_argument('--redirect-uri', action='append', default=[], required=True,
                   help='回调地址，可重复；必须和业务系统实际用的一字不差')
    p.add_argument('--logout-uri', help='单点登出通知地址，可不填')
    p.add_argument('--home-url', help='系统首页，登出后回跳用')
    p.add_argument('--secret', help='不写就随机生成')
    p.add_argument('--force', action='store_true', help='覆盖同名配置')
    p.set_defaults(func=cmd_addclient)

    p = sub.add_parser('clients', help='列出业务系统')
    p.add_argument('--show-secret', action='store_true')我现在已经将项目名称修改为了门户 Portal 需要将原本的homePage相关的代码等进行更新 
    p.set_defaults(func=cmd_clients)

    p = sub.add_parser('delclient', help='删除业务系统')
    p.add_argument('client_id')
    p.set_defaults(func=cmd_delclient)

    return parser


if __name__ == '__main__':
    parsed = build_parser().parse_args()
    db.init_db()
    sys.exit(parsed.func(parsed) or 0)
