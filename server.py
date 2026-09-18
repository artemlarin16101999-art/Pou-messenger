from aiohttp import web
import json
import os
import time
import asyncio
import hashlib
from datetime import datetime

messages = []
users_online = {}
verified_users = set()
banned_users = set()
muted_users = {}
user_info = {}
registered_users = {}   # {username: {'password_hash': '...', 'salt': '...', 'created': ts, 'verified': bool, 'banned': bool}}
lock = asyncio.Lock()

ONLINE_TIMEOUT = 30
MAX_HISTORY = 500
ADMIN_NAME = 'POUADMINISTRATOR'
ADMIN_PASSWORD = 'admin123'   # ⚠️ СМЕНИТЕ ПОСЛЕ ПЕРВОГО ВХОДА!

def hash_password(password, salt=None):
    if salt is None:
        salt = os.urandom(16).hex()
    h = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 100000)
    return h.hex(), salt

async def index(request):
    path = os.path.join(os.path.dirname(__file__), 'static', 'index.html')
    return web.FileResponse(path)

def is_muted(user):
    until = muted_users.get(user)
    if until is None:
        return False
    if until == 0:
        return True
    if time.time() > until:
        del muted_users[user]
        return False
    return True

def mute_info(user):
    until = muted_users.get(user)
    if until is None:
        return None
    if until == 0:
        return 'навсегда'
    left = int(until - time.time())
    if left < 60:
        return f'{left} сек'
    if left < 3600:
        return f'{left // 60} мин'
    return f'{left // 3600} ч'

def time_ago(ts):
    if not ts:
        return 'никогда'
    diff = int(time.time() - ts)
    if diff < 60:
        return f'{diff} сек назад'
    if diff < 3600:
        return f'{diff // 60} мин назад'
    if diff < 86400:
        return f'{diff // 3600} ч назад'
    return f'{diff // 86400} дн назад'

def fmt_time(ts):
    if not ts:
        return '—'
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')

def get_client_ip(request):
    for header in ('X-Forwarded-For', 'X-Real-IP', 'CF-Connecting-IP'):
        val = request.headers.get(header)
        if val:
            return val.split(',')[0].strip()
    peername = request.transport.get_extra_info('peername')
    if peername:
        return peername[0]
    return 'неизвестен'

def register_user(request, name):
    if not name:
        return
    ip = get_client_ip(request)
    ua = request.headers.get('User-Agent', 'неизвестно')[:200]
    now = time.time()
    if name not in user_info:
        user_info[name] = {
            'first_seen': now, 'last_seen': now,
            'msg_count': 0, 'ip': ip, 'ua': ua, 'sessions': 1
        }
    else:
        info = user_info[name]
        if now - info['last_seen'] > ONLINE_TIMEOUT:
            info['sessions'] += 1
        info['last_seen'] = now
        info['ip'] = ip
        info['ua'] = ua

# ============ АВТОРИЗАЦИЯ ============
async def auth(request):
    data = await request.json()
    action = data.get('action')
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    if not username or not password:
        return web.json_response({'ok': False, 'error': 'Заполните все поля'})
    if len(username) > 30 or len(password) > 100:
        return web.json_response({'ok': False, 'error': 'Слишком длинные данные'})

    async with lock:
        # Регистрация
        if action == 'register':
            if username in registered_users:
                return web.json_response({'ok': False, 'error': 'Имя уже занято'})
            if username == ADMIN_NAME:
                return web.json_response({'ok': False, 'error': 'Это имя зарезервировано'})
            pw_hash, salt = hash_password(password)
            registered_users[username] = {
                'password_hash': pw_hash, 'salt': salt,
                'created': time.time(),
                'verified': False, 'banned': False
            }
            return web.json_response({'ok': True, 'message': 'Регистрация успешна'})

        # Вход
        elif action == 'login':
            # Специальный случай: админ
            if username == ADMIN_NAME:
                if password == ADMIN_PASSWORD:
                    return web.json_response({'ok': True, 'is_admin': True})
                return web.json_response({'ok': False, 'error': 'Неверный пароль админа'})

            if username not in registered_users:
                return web.json_response({'ok': False, 'error': 'Пользователь не найден'})
            u = registered_users[username]
            if u.get('banned'):
                return web.json_response({'ok': False, 'error': 'Вы забанены'})
            pw_hash, _ = hash_password(password, u['salt'])
            if pw_hash != u['password_hash']:
                return web.json_response({'ok': False, 'error': 'Неверный пароль'})
            return web.json_response({'ok': True})

        return web.json_response({'ok': False, 'error': 'Неизвестное действие'})

# ============ СООБЩЕНИЯ ============
async def send_message(request):
    data = await request.json()
    sender = data.get('from', 'Guest')
    text = data.get('text', '')
    target = data.get('to')

    async with lock:
        register_user(request, sender)

        if sender in banned_users:
            return web.json_response({'ok': False, 'error': 'Вы забанены'})

        if sender == ADMIN_NAME and text.startswith('/'):
            reply = await handle_admin_command(text, sender)
            if reply:
                return web.json_response({'ok': True, 'system_reply': reply})

        if is_muted(sender) and sender != ADMIN_NAME:
            return web.json_response({'ok': False, 'error': f'Вы в муте ({mute_info(sender)})'})

        msg = {
            'from': sender, 'text': text,
            'time': datetime.now().strftime('%H:%M:%S'),
            'timestamp': time.time(), 'to': target,
            'verified': sender in verified_users
        }
        messages.append(msg)
        if len(messages) > MAX_HISTORY:
            messages[:] = messages[-MAX_HISTORY:]
        if sender in user_info:
            user_info[sender]['msg_count'] += 1
        users_online[sender] = time.time()

    return web.json_response({'ok': True})

async def handle_admin_command(text, admin):
    parts = text.strip().split()
    cmd = parts[0].lower()

    if cmd == '/help':
        return ('👑 КОМАНДЫ:\n'
                '/ban <имя> | /unban <имя>\n'
                '/mute <имя> [сек] | /unmute <имя>\n'
                '/kick <имя>\n'
                '/verify <имя> | /unverify <имя>\n'
                '/verified | /banned | /muted | /users\n'
                '/clearchat [имя]\n'
                '/info <имя>')

    if cmd == '/ban' and len(parts) >= 2:
        u = parts[1]
        if u == ADMIN_NAME: return '❌ Нельзя забанить админа'
        banned_users.add(u)
        if u in registered_users:
            registered_users[u]['banned'] = True
        users_online.pop(u, None)
        return f'🚫 {u} забанен'

    if cmd == '/unban' and len(parts) >= 2:
        banned_users.discard(parts[1])
        if parts[1] in registered_users:
            registered_users[parts[1]]['banned'] = False
        return f'✅ {parts[1]} разбанен'

    if cmd == '/banned':
        return '🚫 Забанены: ' + (', '.join(sorted(banned_users)) if banned_users else 'пусто')

    if cmd == '/mute' and len(parts) >= 2:
        u = parts[1]
        if u == ADMIN_NAME: return '❌ Нельзя замутить админа'
        dur = 0
        if len(parts) >= 3:
            try: dur = int(parts[2])
            except: return '❌ Секунды числом'
        muted_users[u] = 0 if dur == 0 else time.time() + dur
        return f'🔇 {u} в муте' + ('' if dur else ' навсегда')

    if cmd == '/unmute' and len(parts) >= 2:
        muted_users.pop(parts[1], None)
        return f'🔊 {parts[1]} размучен'

    if cmd == '/muted':
        if muted_users:
            return '🔇 В муте: ' + ', '.join(f'{u}({mute_info(u)})' for u in sorted(muted_users))
        return 'Список пуст'

    if cmd == '/kick' and len(parts) >= 2:
        u = parts[1]
        if u == ADMIN_NAME: return '❌ Нельзя'
        if u in users_online:
            users_online.pop(u, None)
            return f'👢 {u} кикнут'
        return f'⚠ {u} не в сети'

    if cmd == '/verify' and len(parts) >= 2:
        verified_users.add(parts[1])
        if parts[1] in registered_users:
            registered_users[parts[1]]['verified'] = True
        return f'✅ {parts[1]} верифицирован'

    if cmd == '/unverify' and len(parts) >= 2:
        verified_users.discard(parts[1])
        if parts[1] in registered_users:
            registered_users[parts[1]]['verified'] = False
        return f'❌ {parts[1]} лишён'

    if cmd == '/verified':
        return '✅ Верифицированы: ' + (', '.join(sorted(verified_users)) if verified_users else 'пусто')

    if cmd == '/clearchat':
        if len(parts) >= 2:
            u = parts[1]
            before = len(messages)
            messages[:] = [m for m in messages if not (
                (m.get('from') == u and m.get('to')) or (m.get('to') == u)
            )]
            return f'🗑 Удалено {before - len(messages)} сообщений с {u}'
        count = len(messages)
        messages.clear()
        return f'🗑 Очищено {count} сообщений'

    if cmd == '/users':
        return '👥 Онлайн: ' + (', '.join(sorted(users_online)) if users_online else 'никого')

    if cmd == '/info' and len(parts) >= 2:
        u = parts[1]
        info = user_info.get(u)
        if not info:
            return f'❓ {u} не найден'
        L = []
        L.append(f'📋 ИНФО: {u}')
        L.append('━━━━━━━━━━━━━━━━━')
        L.append(f'🌐 Статус: {"онлайн" if u in users_online else "офлайн"}')
        if u == ADMIN_NAME: L.append('👑 Админ')
        if u in verified_users: L.append('✅ Верифицирован')
        if u in banned_users: L.append('🚫 ЗАБАНЕН')
        if is_muted(u): L.append(f'🔇 Мут: {mute_info(u)}')
        L.append(f'📅 Первый вход: {fmt_time(info["first_seen"])}')
        L.append(f'🕐 Активность: {time_ago(info["last_seen"])}')
        L.append(f'⏱ Сессий: {info["sessions"]}')
        L.append(f'💬 Сообщений: {info["msg_count"]}')
        L.append('━━━━━━━━━━━━━━━━━')
        L.append(f'🌐 IP: {info["ip"]}')
        ua = info['ua']
        if 'Android' in ua: dev = 'Android'
        elif 'iPhone' in ua or 'iPad' in ua: dev = 'iOS'
        elif 'Windows' in ua: dev = 'Windows'
        elif 'Mac' in ua: dev = 'Mac'
        elif 'Linux' in ua: dev = 'Linux'
        else: dev = ua[:40]
        L.append(f'🖥 Устройство: {dev}')
        return '\n'.join(L)

    return None

async def get_messages(request):
    try:
        since = float(request.query.get('since', 0))
    except ValueError:
        since = 0
    user = request.query.get('user', '')

    async with lock:
        if user:
            register_user(request, user)
            if user in banned_users:
                return web.json_response({'banned': True, 'reason': 'Вы забанены'})
            users_online[user] = time.time()

        now = time.time()
        expired = [u for u, t in users_online.items() if now - t > ONLINE_TIMEOUT]
        for u in expired:
            del users_online[u]

        new_msgs = []
        for m in messages:
            if m['timestamp'] <= since: continue
            if m.get('system') and m.get('to') != user: continue
            if m.get('to') is None or m.get('system'):
                new_msgs.append(m)
            elif m['to'] == user or m['from'] == user:
                new_msgs.append(m)

        return web.json_response({
            'messages': new_msgs,
            'online': list(users_online.keys()),
            'verified': list(verified_users),
            'banned': list(banned_users),
            'muted': {u: mute_info(u) for u in muted_users},
            'now': now,
            'am_admin': user == ADMIN_NAME,
            'am_banned': user in banned_users,
            'am_muted': is_muted(user) and user != ADMIN_NAME
        })

async def get_history(request):
    user = request.query.get('user', '')
    async with lock:
        register_user(request, user)
        hist = [m for m in messages if not m.get('system') or m.get('to') == user]
        return web.json_response({
            'messages': hist[-300:],
            'verified': list(verified_users),
            'am_admin': user == ADMIN_NAME
        })

async def jitsi_room(request):
    a = request.query.get('a', 'user1')
    b = request.query.get('b', 'user2')
    names = sorted([a, b])
    room = 'pou-' + names[0] + '-' + names[1]
    room = ''.join(c if c.isalnum() or c == '-' else '-' for c in room)
    return web.json_response({'room': room})

async def main():
    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/index.html', index)
    app.router.add_post('/auth', auth)
    app.router.add_post('/send', send_message)
    app.router.add_get('/messages', get_messages)
    app.router.add_get('/history', get_history)
    app.router.add_get('/room', jitsi_room)

    port = int(os.environ.get('PORT', 8080))
    print(f'[POU] Запуск на порту {port}')
    print(f'[POU] Админ: {ADMIN_NAME} / пароль: {ADMIN_PASSWORD}')
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print('[POU] Сервер готов')

    async def cleanup():
        while True:
            await asyncio.sleep(10)
            async with lock:
                now = time.time()
                expired = [u for u, t in users_online.items() if now - t > ONLINE_TIMEOUT]
                for u in expired: del users_online[u]
                expired_mutes = [u for u, t in muted_users.items() if t != 0 and now > t]
                for u in expired_mutes: del muted_users[u]
    asyncio.create_task(cleanup())
    await asyncio.Future()

if __name__ == '__main__':
    asyncio.run(main())
