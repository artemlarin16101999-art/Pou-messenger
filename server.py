from aiohttp import web
import json
import os
import time
import asyncio
import hashlib
from datetime import datetime

DATA_FILE = 'pou_data.json'
GARDEN_FILE = 'garden_data.json'
lock = asyncio.Lock()

ONLINE_TIMEOUT = 30
MAX_HISTORY = 500
ADMIN_NAME = 'POUADMINISTRATOR'
ADMIN_PASSWORD = 'admin123'
GARDEN_ADMIN = 'gildi'

# ============ ХРАНИЛИЩЕ ============
messages = []
users_online = {}
verified_users = set()
banned_users = set()
muted_users = {}
user_info = {}
registered_users = {}
active_calls = {}
sessions = {}

# === POUS GARDEN ===
garden_players = {}
GARDEN_TIMEOUT = 10
garden_messages = []
GARDEN_MSG_LIMIT = 50
garden_accounts = {}
garden_sessions = {}

# ============ GARDEN: СОХРАНЕНИЕ ============
def save_garden():
    try:
        data = {
            'accounts': garden_accounts,
            'messages': garden_messages[-GARDEN_MSG_LIMIT:]
        }
        with open(GARDEN_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'[GARDEN] Ошибка сохранения: {e}')

def load_garden():
    global garden_accounts, garden_messages
    if not os.path.exists(GARDEN_FILE):
        print('[GARDEN] Старт с нуля')
        return
    try:
        with open(GARDEN_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        garden_accounts = data.get('accounts', {})
        garden_messages = data.get('messages', [])
        if GARDEN_ADMIN in garden_accounts:
            garden_accounts[GARDEN_ADMIN]['is_admin'] = True
        print(f'[GARDEN] Загружено: {len(garden_accounts)} аккаунтов')
    except Exception as e:
        print(f'[GARDEN] Ошибка загрузки: {e}')

# ============ GARDEN: ХЕШИ ============
def garden_hash(password, salt=None):
    if salt is None:
        salt = os.urandom(16).hex()
    h = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 100000)
    return h.hex(), salt

def garden_create_token(name):
    token = os.urandom(32).hex()
    garden_sessions[token] = {'name': name, 'time': time.time()}
    return token

def garden_check_token(token):
    if not token:
        return None
    sess = garden_sessions.get(token)
    if not sess:
        return None
    if time.time() - sess['time'] > 7 * 86400:
        del garden_sessions[token]
        return None
    return sess['name']

# ============ GARDEN: РЕГИСТРАЦИЯ / ВХОД ============
async def garden_register(request):
    data = await request.json()
    name = (data.get('name') or '').strip()[:15]
    password = data.get('password') or ''
    if not name or not password:
        return web.json_response({'ok': False, 'error': 'Заполни поля'})
    if len(password) < 4:
        return web.json_response({'ok': False, 'error': 'Пароль минимум 4 символа'})

    async with lock:
        if name in garden_accounts:
            return web.json_response({'ok': False, 'error': 'Имя занято'})

        pw_hash, salt = garden_hash(password)
        is_admin = (name.lower() == GARDEN_ADMIN.lower())
        coins = 100000 if is_admin else 500

        garden_accounts[name] = {
            'password_hash': pw_hash,
            'salt': salt,
            'coins': coins,
            'is_admin': is_admin,
            'created': time.time()
        }
        save_garden()
        token = garden_create_token(name)
        return web.json_response({
            'ok': True,
            'token': token,
            'name': name,
            'coins': coins,
            'is_admin': is_admin
        })

async def garden_login(request):
    data = await request.json()
    name = (data.get('name') or '').strip()
    password = data.get('password') or ''
    if not name or not password:
        return web.json_response({'ok': False, 'error': 'Заполни поля'})

    async with lock:
        actual_name = None
        if name in garden_accounts:
            actual_name = name
        else:
            for n in garden_accounts:
                if n.lower() == name.lower():
                    actual_name = n
                    break

        if not actual_name:
            return web.json_response({'ok': False, 'error': 'Аккаунт не найден'})

        acc = garden_accounts[actual_name]
        pw_hash, _ = garden_hash(password, acc['salt'])
        if pw_hash != acc['password_hash']:
            return web.json_response({'ok': False, 'error': 'Неверный пароль'})

        if actual_name.lower() == GARDEN_ADMIN.lower():
            acc['is_admin'] = True

        token = garden_create_token(actual_name)
        return web.json_response({
            'ok': True,
            'token': token,
            'name': actual_name,
            'coins': acc['coins'],
            'is_admin': acc.get('is_admin', False)
        })

async def garden_check_session(request):
    data = await request.json()
    token = (data.get('token') or '').strip()
    name = garden_check_token(token)
    if not name:
        return web.json_response({'ok': False})
    acc = garden_accounts.get(name)
    if not acc:
        return web.json_response({'ok': False})
    return web.json_response({
        'ok': True,
        'name': name,
        'coins': acc['coins'],
        'is_admin': acc.get('is_admin', False)
    })

async def garden_save_coins(request):
    data = await request.json()
    token = (data.get('token') or '').strip()
    name = garden_check_token(token)
    if not name:
        return web.json_response({'ok': False, 'error': 'Не авторизован'})
    coins = int(data.get('coins', 0))
    async with lock:
        if name in garden_accounts:
            garden_accounts[name]['coins'] = coins
            save_garden()
    return web.json_response({'ok': True})

# ============ GARDEN: АДМИН ============
async def garden_admin_kick(request):
    data = await request.json()
    token = (data.get('token') or '').strip()
    admin_name = garden_check_token(token)
    if not admin_name or admin_name.lower() != GARDEN_ADMIN.lower():
        return web.json_response({'ok': False, 'error': 'Нет доступа'})
    target = (data.get('target') or '').strip()
    async with lock:
        if target in garden_players:
            del garden_players[target]
            return web.json_response({'ok': True})
    return web.json_response({'ok': False, 'error': 'Игрок не найден'})

async def garden_admin_give(request):
    data = await request.json()
    token = (data.get('token') or '').strip()
    admin_name = garden_check_token(token)
    if not admin_name or admin_name.lower() != GARDEN_ADMIN.lower():
        return web.json_response({'ok': False, 'error': 'Нет доступа'})
    target = (data.get('target') or '').strip()
    amount = int(data.get('amount', 0))
    async with lock:
        if target in garden_accounts:
            garden_accounts[target]['coins'] += amount
            save_garden()
            return web.json_response({'ok': True, 'coins': garden_accounts[target]['coins']})
    return web.json_response({'ok': False, 'error': 'Аккаунт не найден'})

async def garden_admin_list(request):
    data = await request.json()
    token = (data.get('token') or '').strip()
    admin_name = garden_check_token(token)
    if not admin_name or admin_name.lower() != GARDEN_ADMIN.lower():
        return web.json_response({'ok': False, 'error': 'Нет доступа'})
    async with lock:
        players = []
        for name, p in garden_players.items():
            acc = garden_accounts.get(name, {})
            players.append({
                'name': name,
                'coins': acc.get('coins', 0),
                'x': p.get('x', 0),
                'z': p.get('z', 0)
            })
        return web.json_response({'ok': True, 'players': players})

async def garden_admin_stats(request):
    async with lock:
        return web.json_response({
            'ok': True,
            'total_accounts': len(garden_accounts),
            'online': len(garden_players),
            'admin': GARDEN_ADMIN
        })

# ============ GARDEN: ИГРОВОЙ ============
async def garden_update(request):
    try:
        data = await request.json()
    except:
        return web.json_response({'ok': False, 'error': 'bad json'})
    name = (data.get('name') or '').strip()
    if not name:
        return web.json_response({'ok': False, 'error': 'no name'})
    async with lock:
        now = time.time()
        garden_players[name] = {
            'x': float(data.get('x', 0)),
            'z': float(data.get('z', 0)),
            'yaw': float(data.get('yaw', 0)),
            'skin': data.get('skin', '🐧'),
            'coins': int(data.get('coins', 0)),
            'time': now
        }
        expired = [n for n, p in garden_players.items() if now - p['time'] > GARDEN_TIMEOUT]
        for n in expired:
            del garden_players[n]
        others = []
        for n, p in garden_players.items():
            if n == name:
                continue
            others.append({
                'name': n,
                'x': p['x'], 'z': p['z'], 'yaw': p['yaw'],
                'skin': p['skin'], 'coins': p['coins']
            })
        return web.json_response({'ok': True, 'others': others})

async def garden_stats(request):
    async with lock:
        return web.json_response({
            'ok': True,
            'online': len(garden_players),
            'players': [{'name': n, 'coins': p.get('coins', 0)} for n, p in garden_players.items()]
        })

async def garden_chat_send(request):
    try:
        data = await request.json()
    except:
        return web.json_response({'ok': False, 'error': 'bad json'})
    name = (data.get('name') or '').strip()[:15]
    text = (data.get('text') or '').strip()[:200]
    if not name or not text:
        return web.json_response({'ok': False, 'error': 'empty'})
    colors = ['#f38ba8', '#fab387', '#f9e2af', '#a6e3a1',
              '#89dceb', '#89b4fa', '#cba6f7', '#f5c2e7']
    color_idx = sum(ord(c) for c in name) % len(colors)
    async with lock:
        msg = {
            'name': name,
            'text': text,
            'time': time.time(),
            'color': colors[color_idx]
        }
        garden_messages.append(msg)
        if len(garden_messages) > GARDEN_MSG_LIMIT:
            garden_messages[:] = garden_messages[-GARDEN_MSG_LIMIT:]
        save_garden()
    return web.json_response({'ok': True})

async def garden_chat_get(request):
    try:
        since = float(request.query.get('since', 0))
    except ValueError:
        since = 0
    async with lock:
        new_msgs = [m for m in garden_messages if m['time'] > since]
        return web.json_response({'ok': True, 'messages': new_msgs})

# ============ МЕССЕНДЖЕР (старое, минимально) ============
async def index(request):
    path = os.path.join(os.path.dirname(__file__), 'static', 'index.html')
    return web.FileResponse(path)

async def gamestrel(request):
    path = os.path.join(os.path.dirname(__file__), 'static', 'gamestrel.html')
    return web.FileResponse(path)

async def tictactoe(request):
    path = os.path.join(os.path.dirname(__file__), 'static', 'tictactoe.html')
    return web.FileResponse(path)

async def poublox(request):
    path = os.path.join(os.path.dirname(__file__), 'static', 'poublox.html')
    return web.FileResponse(path)

async def garden(request):
    path = os.path.join(os.path.dirname(__file__), 'static', 'garden.html')
    return web.FileResponse(path)

# ============ ЗАПУСК ============
async def main():
    load_garden()

    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/index.html', index)
    app.router.add_get('/gamestrel.html', gamestrel)
    app.router.add_get('/tictactoe.html', tictactoe)
    app.router.add_get('/poublox.html', poublox)
    app.router.add_get('/garden.html', garden)

    app.router.add_post('/api/garden/register', garden_register)
    app.router.add_post('/api/garden/login', garden_login)
    app.router.add_post('/api/garden/check-session', garden_check_session)
    app.router.add_post('/api/garden/save-coins', garden_save_coins)
    app.router.add_post('/api/garden/update', garden_update)
    app.router.add_get('/api/garden/stats', garden_stats)
    app.router.add_post('/api/garden/chat/send', garden_chat_send)
    app.router.add_get('/api/garden/chat/get', garden_chat_get)

    app.router.add_post('/api/garden/admin/kick', garden_admin_kick)
    app.router.add_post('/api/garden/admin/give', garden_admin_give)
    app.router.add_post('/api/garden/admin/list', garden_admin_list)
    app.router.add_get('/api/garden/admin/stats', garden_admin_stats)

    port = int(os.environ.get('PORT', 8080))
    print(f'[POU] Запуск на порту {port}')
    print(f'[GARDEN] Админ: {GARDEN_ADMIN}')
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
                expired = [n for n, p in garden_players.items() if now - p['time'] > GARDEN_TIMEOUT]
                for n in expired:
                    del garden_players[n]
    asyncio.create_task(cleanup())

    async def autosave():
        while True:
            await asyncio.sleep(30)
            async with lock:
                save_garden()
    asyncio.create_task(autosave())

    await asyncio.Future()

if __name__ == '__main__':
    asyncio.run(main())
