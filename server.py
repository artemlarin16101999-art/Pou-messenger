from aiohttp import web
import json
import os
import time
import asyncio
from datetime import datetime

messages = []
users_online = {}       # {username: last_seen}
verified_users = set()
banned_users = set()
muted_users = {}        # {username: until_timestamp}
user_info = {}          # {username: {'first_seen': ts, 'last_seen': ts, 'msg_count': N, 'ip': 'x.x.x.x', 'ua': '...', 'sessions': N}}
lock = asyncio.Lock()

ONLINE_TIMEOUT = 30
MAX_HISTORY = 500
ADMIN_NAME = 'POUADMINISTRATOR'

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
    """Получаем IP клиента (может быть прокси)"""
    # Проверяем заголовки прокси
    for header in ('X-Forwarded-For', 'X-Real-IP', 'CF-Connecting-IP'):
        val = request.headers.get(header)
        if val:
            return val.split(',')[0].strip()
    # Прямой IP
    peername = request.transport.get_extra_info('peername')
    if peername:
        return peername[0]
    return 'неизвестен'

def register_user(request, name):
    """Обновляем инфу о пользователе при каждом запросе"""
    if not name:
        return
    ip = get_client_ip(request)
    ua = request.headers.get('User-Agent', 'неизвестно')[:200]
    now = time.time()
    if name not in user_info:
        user_info[name] = {
            'first_seen': now,
            'last_seen': now,
            'msg_count': 0,
            'ip': ip,
            'ua': ua,
            'sessions': 1
        }
    else:
        info = user_info[name]
        # Считаем новую сессию, если давно не было активности
        if now - info['last_seen'] > ONLINE_TIMEOUT:
            info['sessions'] += 1
        info['last_seen'] = now
        info['ip'] = ip
        info['ua'] = ua

async def send_message(request):
    data = await request.json()
    sender = data.get('from', 'Guest')
    text = data.get('text', '')
    target = data.get('to')

    async with lock:
        # Обновляем инфу
        register_user(request, sender)

        # Бан
        if sender in banned_users:
            return web.json_response({'ok': False, 'error': 'Вы забанены'})

        # Команды админа
        if sender == ADMIN_NAME and text.startswith('/'):
            reply = await handle_admin_command(text, sender, request)
            if reply:
                return web.json_response({'ok': True, 'system_reply': reply})

        # Мут
        if is_muted(sender) and sender != ADMIN_NAME:
            return web.json_response({
                'ok': False,
                'error': f'Вы в муте ({mute_info(sender)})'
            })

        # Сохраняем сообщение
        msg = {
            'from': sender,
            'text': text,
            'time': datetime.now().strftime('%H:%M:%S'),
            'timestamp': time.time(),
            'to': target,
            'verified': sender in verified_users
        }
        messages.append(msg)
        if len(messages) > MAX_HISTORY:
            messages[:] = messages[-MAX_HISTORY:]

        # Считаем сообщения
        if sender in user_info:
            user_info[sender]['msg_count'] += 1

        users_online[sender] = time.time()

    return web.json_response({'ok': True})

async def handle_admin_command(text, admin, request):
    parts = text.strip().split()
    cmd = parts[0].lower()

    if cmd == '/help':
        return ('👑 КОМАНДЫ АДМИНА:\n'
                '/ban <имя> — забанить\n'
                '/unban <имя> — разбанить\n'
                '/mute <имя> [сек] — замутить\n'
                '/unmute <имя> — снять мут\n'
                '/kick <имя> — выкинуть\n'
                '/verify <имя> — выдать ✅\n'
                '/unverify <имя> — снять ✅\n'
                '/verified — список верифицированных\n'
                '/banned — список забаненных\n'
                '/muted — список в муте\n'
                '/users — все онлайн\n'
                '/clearchat [имя] — очистить сообщения\n'
                '/info <имя> — полная инфа о пользователе')

    if cmd == '/ban' and len(parts) >= 2:
        user = parts[1]
        if user == ADMIN_NAME:
            return '❌ Нельзя забанить администратора'
        banned_users.add(user)
        users_online.pop(user, None)
        return f'🚫 {user} забанен'

    if cmd == '/unban' and len(parts) >= 2:
        banned_users.discard(parts[1])
        return f'✅ {parts[1]} разбанен'

    if cmd == '/banned':
        if banned_users:
            return '🚫 Забанены: ' + ', '.join(sorted(banned_users))
        return 'Список забаненных пуст'

    if cmd == '/mute' and len(parts) >= 2:
        user = parts[1]
        if user == ADMIN_NAME:
            return '❌ Нельзя замутить администратора'
        duration = 0
        if len(parts) >= 3:
            try:
                duration = int(parts[2])
            except ValueError:
                return '❌ Длительность числом (секунды)'
        muted_users[user] = 0 if duration == 0 else time.time() + duration
        return f'🔇 {user} в муте' + (' навсегда' if duration == 0 else f' на {duration} сек')

    if cmd == '/unmute' and len(parts) >= 2:
        muted_users.pop(parts[1], None)
        return f'🔊 {parts[1]} размучен'

    if cmd == '/muted':
        if muted_users:
            return '🔇 В муте: ' + ', '.join(f'{u} ({mute_info(u)})' for u in sorted(muted_users))
        return 'Список пуст'

    if cmd == '/kick' and len(parts) >= 2:
        user = parts[1]
        if user == ADMIN_NAME:
            return '❌ Нельзя кикнуть администратора'
        if user in users_online:
            users_online.pop(user, None)
            return f'👢 {user} кикнут'
        return f'⚠ {user} не в сети'

    if cmd == '/verify' and len(parts) >= 2:
        verified_users.add(parts[1])
        return f'✅ {parts[1]} верифицирован'

    if cmd == '/unverify' and len(parts) >= 2:
        verified_users.discard(parts[1])
        return f'❌ {parts[1]} лишён верификации'

    if cmd == '/verified':
        if verified_users:
            return '✅ Верифицированы: ' + ', '.join(sorted(verified_users))
        return 'Список пуст'

    if cmd == '/clearchat':
        if len(parts) >= 2:
            user = parts[1]
            before = len(messages)
            messages[:] = [m for m in messages if not (
                (m.get('from') == user and m.get('to')) or
                (m.get('to') == user)
            )]
            return f'🗑 Удалено {before - len(messages)} сообщений с {user}'
        count = len(messages)
        messages.clear()
        return f'🗑 Очищено {count} сообщений'

    if cmd == '/users':
        if users_online:
            return '👥 Онлайн: ' + ', '.join(sorted(users_online.keys()))
        return 'Никого нет онлайн'

    # ★★★ ПОЛНАЯ ИНФА О ЮЗЕРЕ ★★★
    if cmd == '/info' and len(parts) >= 2:
        user = parts[1]
        info = user_info.get(user)
        if not info:
            return f'❓ {user} не найден в системе'

        lines = []
        lines.append(f'📋 ИНФО: {user}')
        lines.append('━━━━━━━━━━━━━━━━━━')

        # Статус
        is_online = user in users_online
        lines.append(f'🌐 Статус: {"онлайн" if is_online else "офлайн"}')

        # Роли
        if user == ADMIN_NAME:
            lines.append('👑 Администратор')
        if user in verified_users:
            lines.append('✅ Верифицирован')
        if user in banned_users:
            lines.append('🚫 ЗАБАНЕН')
        if is_muted(user):
            lines.append(f'🔇 Мут: {mute_info(user)}')

        # Времена
        lines.append(f'📅 Первый вход: {fmt_time(info["first_seen"])}')
        lines.append(f'🕐 Последняя активность: {time_ago(info["last_seen"])}')
        lines.append(f'⏱ Сессий: {info["sessions"]}')

        # Сообщения
        lines.append(f'💬 Сообщений: {info["msg_count"]}')

        # IP и устройство
        lines.append('━━━━━━━━━━━━━━━━━━')
        lines.append(f'🌐 IP: {info["ip"]}')
        ua = info['ua']
        # Сокращаем User-Agent
        if 'Android' in ua:
            device = 'Android'
            if 'Chrome' in ua:
                device += ' (Chrome)'
            elif 'Firefox' in ua:
                device += ' (Firefox)'
        elif 'iPhone' in ua or 'iPad' in ua:
            device = 'iOS'
        elif 'Windows' in ua:
            device = 'Windows'
        elif 'Mac' in ua:
            device = 'Mac'
        elif 'Linux' in ua:
            device = 'Linux'
        else:
            device = ua[:40]
        lines.append(f'🖥 Устройство: {device}')

        lines.append('━━━━━━━━━━━━━━━━━━')
        lines.append('ℹ️ IP может быть прокси хостинга')

        return '\n'.join(lines)

    return None

async def get_messages(request):
    try:
        since = float(request.query.get('since', 0))
    except ValueError:
        since = 0
    user = request.query.get('user', '')

    async with lock:
        if user:
            # Обновляем инфу при каждом опросе
            register_user(request, user)

            if user in banned_users:
                return web.json_response({
                    'banned': True,
                    'reason': 'Вы забанены администратором'
                })
            users_online[user] = time.time()

        now = time.time()
        expired = [u for u, t in users_online.items() if now - t > ONLINE_TIMEOUT]
        for u in expired:
            del users_online[u]

        new_msgs = []
        for m in messages:
            if m['timestamp'] <= since:
                continue
            if m.get('system') and m.get('to') != user:
                continue
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
        hist = [m for m in messages
                if not m.get('system') or m.get('to') == user]
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
    app.router.add_post('/send', send_message)
    app.router.add_get('/messages', get_messages)
    app.router.add_get('/history', get_history)
    app.router.add_get('/room', jitsi_room)

    port = int(os.environ.get('PORT', 8080))
    print(f'[POU] Запуск на порту {port}')
    print(f'[POU] Администратор: {ADMIN_NAME}')
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
                for u in expired:
                    del users_online[u]
                expired_mutes = [u for u, t in muted_users.items()
                                 if t != 0 and now > t]
                for u in expired_mutes:
                    del muted_users[u]
    asyncio.create_task(cleanup())
    await asyncio.Future()

if __name__ == '__main__':
    asyncio.run(main())
