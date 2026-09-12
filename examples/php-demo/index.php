<?php
/**
 * 业务系统接入示例（PHP，零依赖）。
 *
 * 跑起来：
 *     php -S 127.0.0.1:8803 examples/php-demo/index.php
 * 先在 server 目录注册它：
 *     python manage.py addclient demo-php --name "PHP 示例" \
 *         --redirect-uri http://127.0.0.1:8803/sso/callback \
 *         --logout-uri  http://127.0.0.1:8803/sso/logout-notify \
 *         --home-url    http://127.0.0.1:8803/
 * 把打印出来的 client_secret 填到下面 CLIENT_SECRET。
 */

// ------------------------------------------------------------------ 配置
const SSO_BASE      = 'http://127.0.0.1:9920';     // 主页 / 认证中心
const SELF_BASE     = 'http://127.0.0.1:8803';     // 本系统
const CLIENT_ID     = 'demo-php';
const CLIENT_SECRET = '把 addclient 打印的 secret 填这里';

const COOKIE   = 'demo_php_session';
const CALLBACK = SELF_BASE . '/sso/callback';

/**
 * 本地会话。这里用文件存是为了示例不依赖任何扩展；
 * 真实项目用你原本的 session 机制即可，唯一的要求是
 * 能按 sso_sid 反查会话，单点登出才踢得掉。
 */
function session_dir(): string {
    $dir = sys_get_temp_dir() . '/sso-php-demo';
    if (!is_dir($dir)) mkdir($dir, 0700, true);
    return $dir;
}

function session_put(string $sid, array $data): void {
    file_put_contents(session_dir() . '/' . $sid . '.json', json_encode($data));
}

function session_get(string $sid): ?array {
    if ($sid === '' || !preg_match('/^[A-Za-z0-9_-]+$/', $sid)) return null;
    $file = session_dir() . '/' . $sid . '.json';
    if (!is_file($file)) return null;
    return json_decode(file_get_contents($file), true) ?: null;
}

function session_drop(string $sid): void {
    if ($sid === '' || !preg_match('/^[A-Za-z0-9_-]+$/', $sid)) return;
    @unlink(session_dir() . '/' . $sid . '.json');
}

/** 单点登出：按主页那边的会话号把本地会话全清掉 */
function session_drop_by_sso(string $ssoSid): void {
    foreach (glob(session_dir() . '/*.json') as $file) {
        $data = json_decode(file_get_contents($file), true);
        if (($data['sso_sid'] ?? '') === $ssoSid) @unlink($file);
    }
}

function current_user(): ?array {
    return session_get($_COOKIE[COOKIE] ?? '')['user'] ?? null;
}

function redirect(string $to): void {
    header('Location: ' . $to, true, 302);
    exit;
}

/** 后端对后端的 POST。有 curl 用 curl，没有就退回流封装 */
function post_json(string $url, array $payload): array {
    $body = json_encode($payload);
    if (function_exists('curl_init')) {
        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_POST           => true,
            CURLOPT_POSTFIELDS     => $body,
            CURLOPT_HTTPHEADER     => ['Content-Type: application/json'],
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT        => 5,
        ]);
        $text   = curl_exec($ch);
        $status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);
    } else {
        $ctx = stream_context_create(['http' => [
            'method'        => 'POST',
            'header'        => "Content-Type: application/json\r\n",
            'content'       => $body,
            'timeout'       => 5,
            'ignore_errors' => true,
        ]]);
        $text   = @file_get_contents($url, false, $ctx);
        $status = isset($http_response_header[0])
            ? (int) explode(' ', $http_response_header[0])[1] : 0;
    }
    return ['status' => (int) $status, 'body' => (string) $text];
}

function random_id(int $bytes = 32): string {
    return rtrim(strtr(base64_encode(random_bytes($bytes)), '+/', '-_'), '=');
}

// ------------------------------------------------------------------ 路由
$path   = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
$method = $_SERVER['REQUEST_METHOD'];

// 1. 没登录就去认证中心要票
if ($path === '/sso/login') {
    redirect(SSO_BASE . '/sso/authorize?' . http_build_query([
        'client_id'    => CLIENT_ID,
        'redirect_uri' => CALLBACK,
        'state'        => random_id(8),
    ]));
}

// 2. 拿票换身份，然后建自己的会话
if ($path === '/sso/callback') {
    $ticket = $_GET['ticket'] ?? '';
    if ($ticket === '') {
        http_response_code(400);
        exit('缺少 ticket');
    }

    // 关键：这一步是后端对后端，client_secret 绝不能出现在浏览器里
    $resp = post_json(SSO_BASE . '/sso/validate', [
        'client_id'     => CLIENT_ID,
        'client_secret' => CLIENT_SECRET,
        'ticket'        => $ticket,
    ]);
    if ($resp['status'] !== 200) {
        http_response_code(401);
        exit('票据校验失败：' . htmlspecialchars($resp['body']));
    }

    $data     = json_decode($resp['body'], true);
    $localSid = random_id();
    session_put($localSid, ['user' => $data['user'], 'sso_sid' => $data['sid']]);

    setcookie(COOKIE, $localSid, ['httponly' => true, 'samesite' => 'Lax', 'path' => '/']);
    redirect('/');
}

// 3. 主页退出时会通知过来，把对应的本地会话销毁
if ($path === '/sso/logout-notify' && $method === 'POST') {
    $body = json_decode(file_get_contents('php://input'), true) ?: [];
    if (!hash_equals(CLIENT_SECRET, (string) ($body['secret'] ?? ''))) {
        http_response_code(403);
        exit('forbidden');
    }
    session_drop_by_sso((string) ($body['sid'] ?? ''));
    header('Content-Type: application/json');
    exit(json_encode(['ok' => true]));
}

if ($path === '/logout') {
    session_drop($_COOKIE[COOKIE] ?? '');
    setcookie(COOKIE, '', ['expires' => 1, 'path' => '/']);
    redirect('/');
}

if ($path === '/logout-all') {
    session_drop($_COOKIE[COOKIE] ?? '');
    setcookie(COOKIE, '', ['expires' => 1, 'path' => '/']);
    redirect(SSO_BASE . '/sso/logout?' . http_build_query(['client_id' => CLIENT_ID]));
}

$user = current_user();
if ($user === null) redirect('/sso/login');

$name  = htmlspecialchars($user['display_name']);
$login = htmlspecialchars($user['username']);
$roles = htmlspecialchars(implode(', ', $user['roles'] ?: [])) ?: '无';
echo <<<HTML
<!doctype html><meta charset="utf-8">
<title>PHP 示例系统</title>
<style>body{font:15px/1.8 system-ui,"Microsoft YaHei",sans-serif;padding:48px;max-width:680px}
code{background:#f2f4f7;padding:2px 6px;border-radius:4px}</style>
<h2>这里是「PHP 示例系统」</h2>
<p>当前用户：<b>$name</b>（$login）</p>
<p>角色：<code>$roles</code></p>
<p>整个过程没在这个系统里输过账号密码。</p>
<p><a href="/logout">退出本系统</a> ｜ <a href="/logout-all">退出所有系统</a></p>
HTML;
