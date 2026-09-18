from aiohttp import web
import json
import os
import time
import asyncio
import hashlib
import secrets
from datetime import datetime

# ============ Хранилище в памяти ============
users = {}          # {username: {"password_hash": ..., "salt": ..., "friends": set(), "requests": set()}}
sessions = {}       # {token: username}
messages = []       # общий список сообщений
users_online = {}   # {username: last_seen}
lock = asyncio.Lock()

ONLINE_TIMEOUT = 30
MAX_HISTORY = 1000

# ============ Пароли ============
def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 100000)
    return h.hex(), salt

def check_password(password, stored_hash, salt):
    h, _ = hash_password(password, salt)
    return h == stored_hash

# ============ Страницы ============
async def index(request):
    path = os.path.join(os.path.dirname(__file__), 'static', 'index.html')
    return web.FileResponse(path)

async def games_page(request):
    path = os.path.join(os.path.dirname(__file__), 'static', 'games.html')
    return web.FileResponse(path)

# ============ Аутентификация ============
async def register(request):
    data = await request.json()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    if not username or not password:
        return web.json_response({'ok': False, 'error': 'Пустые поля'})
    if len(username) < 2:
        return web.json_response({'ok': False, 'error': 'Имя минимум 2 символа'})
    if len(password) < 3:
        return web.json_response({'ok': False, 'error': 'Пароль минимум 3 символа'})

    async with lock:
        if username in users:
            return web.json_response({'ok': False, 'error': 'Имя уже занято'})
        pw_hash, salt = hash_password(password)
        users[username] = {
            'password_hash': pw_hash,
            'salt': salt,
            'friends': set(),
            'requests': set()
        }
        token = secrets.token_hex(24)
        sessions[token] = username
    return web.json_response({'ok': True, 'token': token, 'username': username})

async def login(request):
    data = await request.json()
    username = data.get('username', '').strip()
    password = data.get('password', '')

    async with lock:
        if username not in users:
            return web.json_response({'ok': False, 'error': 'Пользователь не найден'})
        u = users[username]
        if not check_password(password, u['password_hash'], u['salt']):
            return web.json_response({'ok': False, 'error': 'Неверный пароль'})
        token = secrets.token_hex(24)
        sessions[token] = username
    return web.json_response({'ok': True, 'token': token, 'username': username})

async def logout(request):
    data = await request.json()
    token = data.get('token', '')
    async with lock:
        if token in sessions:
            del sessions[token]
    return web.json_response({'ok': True})

def get_user(request):
    """Получить username по токену из query (?token=...)"""
    token = request.query.get('token', '')
    return sessions.get(token)

def get_user_post(data):
    token = data.get('token', '')
    return sessions.get(token)

# ============ Друзья ============
async def all_users(request):
    """Список всех зарегистрированных пользователей (без паролей)"""
    async with lock:
        return web.json_response({
            'users': list(users.keys())
        })

async def friends_list(request):
    me = get_user(request)
    if not me:
        return web.json_response({'ok': False, 'error': 'Не авторизован'})
    async with lock:
        u = users[me]
        return web.json_response({
            'friends': list(u['friends']),
            'requests': list(u['requests']),
            'sent_requests': [name for name, u2 in users.items() if me in u2['requests']]
        })

async def send_friend_request(request):
    data = await request.json()
    me = get_user_post(data)
    if not me:
        return web.json_response({'ok': False, 'error': 'Не авторизован'})
    target = data.get('target', '').strip()
    if target == me:
        return web.json_response({'ok': False, 'error': 'Нельзя добавить себя'})
    async with lock:
        if target not in users:
            return web.json_response({'ok': False, 'error': 'Пользователь не найден'})
        if target in users[me]['friends']:
            return web.json_response({'ok': False, 'error': 'Уже в друзьях'})
        users[target]['requests'].add(me)
    return web.json_response({'ok': True})

async def accept_friend(request):
    data = await request.json()
    me = get_user_post(data)
    if not me:
        return web.json_response({'ok': False, 'error': 'Не авторизован'})
    target = data.get('target', '').strip()
    async with lock:
        if target not in users[me]['requests']:
            return web.json_response({'ok': False, 'error': 'Нет заявки'})
        users[me]['requests'].discard(target)
        users[me]['friends'].add(target)
        users[target]['friends'].add(me)
    return web.json_response({'ok': True})

async def decline_friend(request):
    data = await request.json()
    me = get_user_post(data)
    if not me:
        return web.json_response({'ok': False, 'error': 'Не авторизован'})
    target = data.get('target', '').strip()
    async with lock:
        users[me]['requests'].discard(target)
    return web.json_response({'ok': True})

async def remove_friend(request):
    data = await request.json()
    me = get_user_post(data)
    if not me:
        return web.json_response({'ok': False, 'error': 'Не авторизован'})
    target = data.get('target', '').strip()
    async with lock:
        users[me]['friends'].discard(target)
        if target in users:
            users[target]['friends'].discard(me)
    return web.json_response({'ok': True})

# ============ Сообщения ============
async def send_message(request):
    data = await request.json()
    me = get_user_post(data)
    if not me:
        return web.json_response({'ok': False, 'error': 'Не авторизован'})

    to = data.get('to')
    # Личные — только друзьям
    if to:
        async with lock:
            if to not in users[me]['friends']:
                return web.json_response({'ok': False, 'error': 'Можно писать только друзьям'})

    async with lock:
        msg = {
            'from': me,
            'text': data.get('text', ''),
            'time': datetime.now().strftime('%H:%M:%S'),
            'timestamp': time.time(),
            'to': to
        }
        messages.append(msg)
        if len(messages) > MAX_HISTORY:
            messages[:] = messages[-MAX_HISTORY:]
        users_online[me] = time.time()
    return web.json_response({'ok': True})

async def get_messages(request):
    me = get_user(request)
    if not me:
        return web.json_response({'ok': False, 'error': 'Не авторизован'}, status=401)

    try:
        since = float(request.query.get('since', 0))
    except ValueError:
        since = 0

    async with lock:
        users_online[me] = time.time()
        now = time.time()
        expired = [u for u, t in users_online.items() if now - t > ONLINE_TIMEOUT]
        for u in expired:
            del users_online[u]

        friends = users[me]['friends']
        new_msgs = []
        for m in messages:
            if m['timestamp'] <= since:
                continue
            if m.get('to') is None:
                # Общий чат — только от друзей (можно ужесточить: только друзья видят)
                if m['from'] in friends or m['from'] == me:
                    new_msgs.append(m)
            elif m['to'] == me or m['from'] == me:
                new_msgs.append(m)

        # Онлайн — только друзья
        friends_online = [u for u in users_online if u in friends]
        return web.json_response({
            'messages': new_msgs,
            'online': friends_online,
            'now': now
        })

async def get_history(request):
    me = get_user(request)
    if not me:
        return web.json_response({'ok': False}, status=401)
    async with lock:
        friends = users[me]['friends']
        hist = []
        for m in messages[-500:]:
            if m.get('to') is None:
                if m['from'] in friends or m['from'] == me:
                    hist.append(m)
            elif m['to'] == me or m['from'] == me:
                hist.append(m)
    return web.json_response({'messages': hist})

# ============ Jitsi ============
async def jitsi_room(request):
    a = request.query.get('a', 'user1')
    b = request.query.get('b', 'user2')
    names = sorted([a, b])
    room = 'pou-' + names[0] + '-' + names[1]
    room = ''.join(c if c.isalnum() or c == '-' else '-' for c in room)
    return web.json_response({'room': room})

# ============ Запуск ============
async def main():
    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/index.html', index)
    app.router.add_get('/games', games_page)
    app.router.add_get('/games.html', games_page)

    app.router.add_post('/register', register)
    app.router.add_post('/login', login)
    app.router.add_post('/logout', logout)

    app.router.add_get('/users', all_users)
    app.router.add_get('/friends', friends_list)
    app.router.add_post('/friend/request', send_friend_request)
    app.router.add_post('/friend/accept', accept_friend)
    app.router.add_post('/friend/decline', decline_friend)
    app.router.add_post('/friend/remove', remove_friend)

    app.router.add_post('/send', send_message)
    app.router.add_get('/messages', get_messages)
    app.router.add_get('/history', get_history)
    app.router.add_get('/room', jitsi_room)

    port = int(os.environ.get('PORT', 8080))
    print(f'[POU] Запуск на порту {port}')
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
    asyncio.create_task(cleanup())
    await asyncio.Future()

if __name__ == '__main__':
    asyncio.run(main())
