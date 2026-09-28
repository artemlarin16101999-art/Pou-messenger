from aiohttp import web
import json
import os
import time
import asyncio
import hashlib
import hmac

DATA_FILE = 'pou_data.json'
GARDEN_FILE = 'garden_data.json'
lock = asyncio.Lock()

ADMIN_NAME = 'POUADMINISTRATOR'
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '123fff123')
GARDEN_ADMIN = 'gildi'

# ============ POUS GARDEN ============
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


def is_admin(name):
    return name and name.lower() == GARDEN_ADMIN.lower()


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
        admin_flag = (name.lower() == GARDEN_ADMIN.lower())
        coins = 100000 if admin_flag else 500

        garden_accounts[name] = {
            'password_hash': pw_hash,
            'salt': salt,
            'coins': coins,
            'is_admin': admin_flag,
            'created': time.time()
        }
        save_garden()
        token = garden_create_token(name)
        return web.json_response({
            'ok': True,
            'token': token,
            'name': name,
            'coins': coins,
            'is_admin': admin_flag
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
        if not hmac.compare_digest(pw_hash, acc['password_hash']):
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


async def garden_get_coins(request):
    data = await request.json()
    token = (data.get('token') or '').strip()
    name = garden_check_token(token)
    if not name:
        return web.json_response({'ok': False, 'error': 'Не авторизован'})
    acc = garden_accounts.get(name)
    if not acc:
        return web.json_response({'ok': False, 'error': 'Аккаунт не найден'})
    return web.json_response({'ok': True, 'coins': acc['coins']})


async def garden_set_coins(request):
    data = await request.json()
    token = (data.get('token') or '').strip()
    name = garden_check_token(token)
    if not name:
        return web.json_response({'ok': False, 'error': 'Не авторизован'})
    try:
        coins = int(data.get('coins', 0))
    except (ValueError, TypeError):
        return web.json_response({'ok': False, 'error': 'bad coins'})
    if coins < 0:
        coins = 0
    async with lock:
        acc = garden_accounts.get(name)
        if not acc:
            return web.json_response({'ok': False, 'error': 'Аккаунт не найден'})
        # Не позволяем ставить монеты больше, чем есть + 1 миллион (античит-заглушка)
        if coins > acc['coins'] + 1_000_000:
            return web.json_response({'ok': False, 'error': 'Слишком много'})
        acc['coins'] = coins
        save_garden()
    return web.json_response({'ok': True, 'coins': coins})


# ============ GARDEN: АДМИН ============
async def garden_admin_kick(request):
    data = await request.json()
    token = (data.get('token') or '').strip()
    admin_name = garden_check_token(token)
    if not is_admin(admin_name):
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
    if not is_admin(admin_name):
        return web.json_response({'ok': False, 'error': 'Нет доступа'})
    target = (data.get('target') or '').strip()
    try:
        amount = int(data.get('amount', 0))
    except (ValueError, TypeError):
        return web.json_response({'ok': False, 'error': 'bad amount'})
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
    if not is_admin(admin_name):
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
    data = await request.json()
    token = (data.get('token') or '').strip()
    admin_name = garden_check_token(token)
    if not is_admin(admin_name):
        return web.json_response({'ok': False, 'error': 'Нет доступа'})
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
    except Exception:
        return web.json_response({'ok': False, 'error': 'bad json'})

    token = (data.get('token') or '').strip()
    name = garden_check_token(token)
    if not name:
        return web.json_response({'ok': False, 'error': 'Не авторизован'})

    async with lock:
        now = time.time()
        acc = garden_accounts.get(name, {})
        garden_players[name] = {
            'x': float(data.get('x', 0)),
            'z': float(data.get('z', 0)),
            'yaw': float(data.get('yaw', 0)),
            'skin': data.get('skin', '🐧'),
            'coins': acc.get('coins', 0),
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


async def garden_leave(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({'ok': False, 'error': 'bad json'})
    token = (data.get('token') or '').strip()
    name = garden_check_token(token)
    if not name:
        return web.json_response({'ok': False, 'error': 'Не авторизован'})
    async with lock:
        garden_players.pop(name, None)
    return web.json_response({'ok': True})


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
    except Exception:
        return web.json_response({'ok': False, 'error': 'bad json'})

    token = (data.get('token') or '').strip()
    name = garden_check_token(token)
    if not name:
        return web.json_response({'ok': False, 'error': 'Не авторизован'})

    text = (data.get('text') or '').strip()[:200]
    if not text:
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


# ============ СТАТИКА ============
def static_handler(filename):
    async def handler(request):
        path = os.path.join(os.path.dirname(__file__), 'static', filename)
        return web.FileResponse(path)
    return handler


# ============ ЗАПУСК ============
async def main():
    load_garden()

    app = web.Application()
    app.router.add_get('/', static_handler('index.html'))
    app.router.add_get('/index.html', static_handler('index.html'))
    app.router.add_get('/gamestrel.html', static_handler('gamestrel.html'))
    app.router.add_get('/tictactoe.html', static_handler('tictactoe.html'))
    app.router.add_get('/poublox.html', static_handler('poublox.html'))
    app.router.add_get('/garden.html', static_handler('garden.html'))

    app.router.add_post('/api/garden/register', garden_register)
    app.router.add_post('/api/garden/login', garden_login)
    app.router.add_post('/api/garden/check-session', garden_check_session)
    app.router.add_post('/api/garden/get-coins', garden_get_coins)
    app.router.add_post('/api/garden/set-coins', garden_set_coins)
    app.router.add_post('/api/garden/update', garden_update)
    app.router.add_post('/api/garden/leave', garden_leave)
    app.router.add_get('/api/garden/stats', garden_stats)
    app.router.add_post('/api/garden/chat/send', garden_chat_send)
    app.router.add_get('/api/garden/chat/get', garden_chat_get)

    app.router.add_post('/api/garden/admin/kick', garden_admin_kick)
    app.router.add_post('/api/garden/admin/give', garden_admin_give)
    app.router.add_post('/api/garden/admin/list', garden_admin_list)
    app.router.add_post('/api/garden/admin/stats', garden_admin_stats)

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

    async def autosave():
        while True:
            await asyncio.sleep(30)
            async with lock:
                save_garden()

    asyncio.create_task(cleanup())
    asyncio.create_task(autosave())
    await asyncio.Future()


if __name__ == '__main__':
    asyncio.run(main())
