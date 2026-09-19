from aiohttp import web
import json
import os
import time
import asyncio
import hashlib
from datetime import datetime

DATA_FILE = 'pou_data.json'
lock = asyncio.Lock()

ONLINE_TIMEOUT = 30
MAX_HISTORY = 500
ADMIN_NAME = 'POUADMINISTRATOR'
ADMIN_PASSWORD = 'admin123'

# ============ ХРАНИЛИЩЕ ============
messages = []
users_online = {}
verified_users = set()
banned_users = set()
muted_users = {}
user_info = {}
registered_users = {}
active_calls = {}  # {caller: {'to': recipient, 'status': 'ringing'|'accepted'|'rejected', 'time': ts}}

# ============ 21 ПОДАРОК ============
GIFTS = [
    {'id': 'coffee',    'name': 'Кофе',       'emoji': '☕',  'price': 5},
    {'id': 'burger',    'name': 'Бургер',     'emoji': '🍔',  'price': 10},
    {'id': 'ball',      'name': 'Мяч',        'emoji': '⚽',  'price': 10},
    {'id': 'pizza',     'name': 'Пицца',      'emoji': '🍕',  'price': 15},
    {'id': 'dice',      'name': 'Кубик',      'emoji': '🎲',  'price': 15},
    {'id': 'rose',      'name': 'Роза',       'emoji': '🌹',  'price': 20},
    {'id': 'sushi',     'name': 'Суши',       'emoji': '🍣',  'price': 25},
    {'id': 'tulip',     'name': 'Тюльпан',    'emoji': '🌷',  'price': 25},
    {'id': 'cake',      'name': 'Торт',       'emoji': '🎂',  'price': 30},
    {'id': 'sunflower', 'name': 'Подсолнух',  'emoji': '🌻',  'price': 35},
    {'id': 'bouquet',   'name': 'Букет',      'emoji': '💐',  'price': 40},
    {'id': 'teddy',     'name': 'Мишка',      'emoji': '🧸',  'price': 50},
    {'id': 'robot',     'name': 'Робот',      'emoji': '🤖',  'price': 80},
    {'id': 'penguin',   'name': 'Пингвин',    'emoji': '🐧',  'price': 100},
    {'id': 'rocket',    'name': 'Ракета',     'emoji': '🚀',  'price': 300},
    {'id': 'bomb',      'name': 'Пушка',      'emoji': '💣',  'price': 500},
    {'id': 'space',     'name': 'Космос',     'emoji': '🌌',  'price': 700},
    {'id': 'ring',      'name': 'Кольцо',     'emoji': '💍',  'price': 800},
    {'id': 'crown',     'name': 'Корона',     'emoji': '👑',  'price': 1500},
    {'id': 'diamond',   'name': 'Алмаз',      'emoji': '💎',  'price': 3000},
    {'id': 'pou',       'name': 'САМ ПУ',     'emoji': '🐧',  'price': 10000}
]

# ============ СОХРАНЕНИЕ ============
def save_data():
    try:
        data = {
            'messages': messages[-MAX_HISTORY:],
            'verified_users': list(verified_users),
            'banned_users': list(banned_users),
            'muted_users': {u: t for u, t in muted_users.items()},
            'user_info': user_info,
            'registered_users': registered_users
        }
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'[POU] Ошибка сохранения: {e}')

def load_data():
    global messages, verified_users, banned_users, muted_users, user_info, registered_users
    if not os.path.exists(DATA_FILE):
        print('[POU] Старт с нуля')
        return
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        messages = data.get('messages', [])
        verified_users = set(data.get('verified_users', []))
        banned_users = set(data.get('banned_users', []))
        muted_users = data.get('muted_users', {})
        user_info = data.get('user_info', {})
        registered_users = data.get('registered_users', {})
        banned_users.discard(ADMIN_NAME)
        for username in list(registered_users.keys()):
            ensure_user_data(username)
        print(f'[POU] Загружено: {len(registered_users)} юзеров')
    except Exception as e:
        print(f'[POU] Ошибка загрузки: {e}')

def hash_password(password, salt=None):
    if salt is None:
        salt = os.urandom(16).hex()
    h = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 100000)
    return h.hex(), salt

# ============ ИГРОВЫЕ ДАННЫЕ ============
def ensure_user_data(username):
    if username not in registered_users:
        return
    u = registered_users[username]
    if 'pours' not in u:
        u['pours'] = 9999999 if username == ADMIN_NAME else 0
    if 'owned_skins' not in u:
        u['owned_skins'] = ['default']
    if 'equipped_skin' not in u:
        u['equipped_skin'] = 'default'
    if 'gifts_received' not in u:
        u['gifts_received'] = []
    if 'gifts_sent' not in u:
        u['gifts_sent'] = []
    if 'ttt_wins' not in u:
        u['ttt_wins'] = 0
    if 'ttt_losses' not in u:
        u['ttt_losses'] = 0
    if 'ttt_draws' not in u:
        u['ttt_draws'] = 0
    if 'last_free_pours' not in u:
        u['last_free_pours'] = 0

def ensure_admin_exists():
    if ADMIN_NAME not in registered_users:
        registered_users[ADMIN_NAME] = {
            'password_hash': '', 'salt': '',
            'created': time.time(),
            'verified': True, 'banned': False,
            'pours': 9999999,
            'owned_skins': ['default', 'gold', 'ruby', 'emerald', 'sapphire',
                            'neon', 'fire', 'ice', 'rainbow', 'cristal'],
            'equipped_skin': 'gold',
            'gifts_received': [], 'gifts_sent': [],
            'ttt_wins': 0, 'ttt_losses': 0, 'ttt_draws': 0,
            'last_free_pours': 0
        }
    else:
        registered_users[ADMIN_NAME]['pours'] = 9999999
        registered_users[ADMIN_NAME]['verified'] = True
        registered_users[ADMIN_NAME]['banned'] = False

def get_user_game_data(username):
    if username == ADMIN_NAME:
        ensure_admin_exists()
    if username not in registered_users:
        return None
    ensure_user_data(username)
    return registered_users[username]

# ============ УТИЛИТЫ ============
def is_muted(user):
    if user == ADMIN_NAME:
        return False
    until = muted_users.get(user)
    if until is None:
        return False
    if until == 0:
        return True
    if time.time() > until:
        del muted_users[user]
        save_data()
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
    for h in ('X-Forwarded-For', 'X-Real-IP', 'CF-Connecting-IP'):
        val = request.headers.get(h)
        if val:
            return val.split(',')[0].strip()
    peer = request.transport.get_extra_info('peername')
    if peer:
        return peer[0]
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

# ============ РОУТЫ HTML ============
async def index(request):
    path = os.path.join(os.path.dirname(__file__), 'static', 'index.html')
    return web.FileResponse(path)

async def gamestrel(request):
    path = os.path.join(os.path.dirname(__file__), 'static', 'gamestrel.html')
    return web.FileResponse(path)

async def tictactoe(request):
    path = os.path.join(os.path.dirname(__file__), 'static', 'tictactoe.html')
    return web.FileResponse(path)

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
        if action == 'register':
            if username == ADMIN_NAME:
                return web.json_response({'ok': False, 'error': 'Имя зарезервировано'})
            if username in registered_users:
                return web.json_response({'ok': False, 'error': 'Имя уже занято'})
            pw_hash, salt = hash_password(password)
            registered_users[username] = {
                'password_hash': pw_hash, 'salt': salt,
                'created': time.time(),
                'verified': False, 'banned': False,
                'pours': 0,
                'owned_skins': ['default'],
                'equipped_skin': 'default',
                'gifts_received': [], 'gifts_sent': [],
                'ttt_wins': 0, 'ttt_losses': 0, 'ttt_draws': 0,
                'last_free_pours': 0
            }
            save_data()
            return web.json_response({'ok': True, 'message': 'Регистрация успешна'})

        elif action == 'login':
            if username == ADMIN_NAME:
                if password == ADMIN_PASSWORD:
                    ensure_admin_exists()
                    save_data()
                    return web.json_response({'ok': True, 'is_admin': True})
                return web.json_response({'ok': False, 'error': 'Неверный пароль админа'})

            if username not in registered_users:
                return web.json_response({'ok': False, 'error': 'Пользователь не найден'})
            u = registered_users[username]
            if u.get('banned') or username in banned_users:
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
        if sender in banned_users and sender != ADMIN_NAME:
            return web.json_response({'ok': False, 'error': 'Вы забанены'})

        if sender == ADMIN_NAME and text.startswith('/'):
            reply = await handle_admin_command(text, sender)
            if reply:
                return web.json_response({'ok': True, 'system_reply': reply})

        if is_muted(sender):
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
        save_data()

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
                '/info <имя>\n'
                '/givepours <имя> <сумма>')

    if cmd == '/ban' and len(parts) >= 2:
        u = parts[1]
        if u == ADMIN_NAME:
            return '❌ Нельзя забанить админа'
        banned_users.add(u)
        if u in registered_users:
            registered_users[u]['banned'] = True
        users_online.pop(u, None)
        save_data()
        return f'🚫 {u} забанен'

    if cmd == '/unban' and len(parts) >= 2:
        banned_users.discard(parts[1])
        if parts[1] in registered_users:
            registered_users[parts[1]]['banned'] = False
        save_data()
        return f'✅ {parts[1]} разбанен'

    if cmd == '/banned':
        return '🚫 Забанены: ' + (', '.join(sorted(banned_users)) if banned_users else 'пусто')

    if cmd == '/mute' and len(parts) >= 2:
        u = parts[1]
        if u == ADMIN_NAME:
            return '❌ Нельзя замутить админа'
        dur = 0
        if len(parts) >= 3:
            try:
                dur = int(parts[2])
            except:
                return '❌ Секунды числом'
        muted_users[u] = 0 if dur == 0 else time.time() + dur
        save_data()
        return f'🔇 {u} в муте' + ('' if dur else ' навсегда')

    if cmd == '/unmute' and len(parts) >= 2:
        muted_users.pop(parts[1], None)
        save_data()
        return f'🔊 {parts[1]} размучен'

    if cmd == '/muted':
        if muted_users:
            return '🔇 В муте: ' + ', '.join(f'{u}({mute_info(u)})' for u in sorted(muted_users))
        return 'Список пуст'

    if cmd == '/kick' and len(parts) >= 2:
        u = parts[1]
        if u == ADMIN_NAME:
            return '❌ Нельзя'
        if u in users_online:
            users_online.pop(u, None)
            return f'👢 {u} кикнут'
        return f'⚠ {u} не в сети'

    if cmd == '/verify' and len(parts) >= 2:
        verified_users.add(parts[1])
        if parts[1] in registered_users:
            registered_users[parts[1]]['verified'] = True
        save_data()
        return f'✅ {parts[1]} верифицирован'

    if cmd == '/unverify' and len(parts) >= 2:
        verified_users.discard(parts[1])
        if parts[1] in registered_users:
            registered_users[parts[1]]['verified'] = False
        save_data()
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
            save_data()
            return f'🗑 Удалено {before - len(messages)} сообщений с {u}'
        count = len(messages)
        messages.clear()
        save_data()
        return f'🗑 Очищено {count} сообщений'

    if cmd == '/users':
        return '👥 Онлайн: ' + (', '.join(sorted(users_online)) if users_online else 'никого')

    if cmd == '/givepours' and len(parts) >= 3:
        u = parts[1]
        try:
            amount = int(parts[2])
        except:
            return '❌ Сумма числом'
        if u not in registered_users:
            return f'❓ {u} не найден'
        ensure_user_data(u)
        registered_users[u]['pours'] = max(0, registered_users[u].get('pours', 0) + amount)
        save_data()
        return f'💰 {u} получил {amount} Pours (итого: {registered_users[u]["pours"]})'

    if cmd == '/info' and len(parts) >= 2:
        u = parts[1]
        info = user_info.get(u)
        if not info:
            return f'❓ {u} не найден'
        L = []
        L.append(f'📋 ИНФО: {u}')
        L.append('━━━━━━━━━━━━━━━━━')
        L.append(f'🌐 Статус: {"онлайн" if u in users_online else "офлайн"}')
        if u == ADMIN_NAME:
            L.append('👑 Админ')
        if u in verified_users:
            L.append('✅ Верифицирован')
        if u in banned_users:
            L.append('🚫 ЗАБАНЕН')
        if is_muted(u):
            L.append(f'🔇 Мут: {mute_info(u)}')
        game = get_user_game_data(u)
        if game:
            L.append(f'💰 Pours: {game.get("pours", 0)}')
            L.append(f'🎮 Крестики: 🏆 {game.get("ttt_wins", 0)} | 💀 {game.get("ttt_losses", 0)} | 🤝 {game.get("ttt_draws", 0)}')
        L.append(f'📅 Первый вход: {fmt_time(info["first_seen"])}')
        L.append(f'🕐 Активность: {time_ago(info["last_seen"])}')
        L.append(f'💬 Сообщений: {info["msg_count"]}')
        L.append('━━━━━━━━━━━━━━━━━')
        L.append(f'🌐 IP: {info["ip"]}')
        ua = info['ua']
        if 'Android' in ua:
            dev = 'Android'
        elif 'iPhone' in ua or 'iPad' in ua:
            dev = 'iOS'
        elif 'Windows' in ua:
            dev = 'Windows'
        elif 'Mac' in ua:
            dev = 'Mac'
        elif 'Linux' in ua:
            dev = 'Linux'
        else:
            dev = ua[:40]
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
            if user in banned_users and user != ADMIN_NAME:
                return web.json_response({'banned': True, 'reason': 'Вы забанены'})
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

        # Входящие звонки
        incoming = None
        for caller, call in list(active_calls.items()):
            if call['to'] == user and call['status'] == 'ringing':
                if now - call['time'] < 30:
                    incoming = {'from': caller, 'time': call['time']}
                else:
                    del active_calls[caller]

        # Статус моего исходящего звонка
        outgoing = None
        if user in active_calls:
            call = active_calls[user]
            if now - call['time'] > 60:
                del active_calls[user]
            else:
                outgoing = call

        return web.json_response({
            'messages': new_msgs,
            'online': list(users_online.keys()),
            'verified': list(verified_users),
            'banned': list(banned_users),
            'muted': {u: mute_info(u) for u in muted_users},
            'now': now,
            'am_admin': user == ADMIN_NAME,
            'am_banned': user in banned_users and user != ADMIN_NAME,
            'am_muted': is_muted(user),
            'incoming_call': incoming,
            'outgoing_call': outgoing
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

# ============ API ПРОФИЛЯ ============
async def get_profile(request):
    data = await request.json()
    username = (data.get('username') or '').strip()
    if not username:
        return web.json_response({'ok': False, 'error': 'Не указано имя'})
    async with lock:
        game = get_user_game_data(username)
        if game is None:
            return web.json_response({'ok': False, 'error': 'Пользователь не найден'})
        save_data()
        return web.json_response({
            'ok': True,
            'pours': game.get('pours', 0),
            'gifts_received': game.get('gifts_received', []),
            'gifts_sent': game.get('gifts_sent', []),
            'ttt_wins': game.get('ttt_wins', 0),
            'ttt_losses': game.get('ttt_losses', 0),
            'ttt_draws': game.get('ttt_draws', 0),
            'last_free_pours': game.get('last_free_pours', 0)
        })

# ============ API ПОДАРКОВ ============
async def get_gifts(request):
    return web.json_response({'ok': True, 'gifts': GIFTS})

async def send_gift(request):
    data = await request.json()
    sender = (data.get('from') or '').strip()
    recipient = (data.get('to') or '').strip()
    gift_id = (data.get('gift_id') or '').strip()
    message = (data.get('message') or '').strip()[:200]

    if not sender or not recipient or not gift_id:
        return web.json_response({'ok': False, 'error': 'Заполните все поля'})
    if sender == recipient:
        return web.json_response({'ok': False, 'error': 'Нельзя подарить себе'})

    gift = next((g for g in GIFTS if g['id'] == gift_id), None)
    if not gift:
        return web.json_response({'ok': False, 'error': 'Подарок не найден'})

    async with lock:
        s = get_user_game_data(sender)
        r = get_user_game_data(recipient)
        if s is None:
            return web.json_response({'ok': False, 'error': 'Отправитель не найден'})
        if r is None:
            return web.json_response({'ok': False, 'error': 'Получатель не найден'})

        if s.get('pours', 0) < gift['price']:
            return web.json_response({'ok': False, 'error': f'Не хватает {gift["price"] - s["pours"]} Pours'})

        s['pours'] -= gift['price']
        gift_record = {
            'gift_id': gift_id,
            'from': sender,
            'time': time.time(),
            'message': message
        }
        r.setdefault('gifts_received', []).append(gift_record)
        s.setdefault('gifts_sent', []).append({
            'gift_id': gift_id,
            'to': recipient,
            'time': time.time(),
            'message': message
        })

        msg = {
            'from': 'СИСТЕМА',
            'text': f'🎁 {sender} подарил вам {gift["emoji"]} {gift["name"]}!' + (f'\n💬 "{message}"' if message else ''),
            'time': datetime.now().strftime('%H:%M:%S'),
            'timestamp': time.time(),
            'to': recipient,
            'system': True
        }
        messages.append(msg)
        if len(messages) > MAX_HISTORY:
            messages[:] = messages[-MAX_HISTORY:]

        save_data()
        return web.json_response({'ok': True, 'gift': gift, 'pours': s['pours']})

# ============ API «ПОЛУЧИТЬ POURS» ============
async def claim_free(request):
    data = await request.json()
    username = (data.get('username') or '').strip()
    if not username:
        return web.json_response({'ok': False, 'error': 'Не указано имя'})

    async with lock:
        game = get_user_game_data(username)
        if game is None:
            return web.json_response({'ok': False, 'error': 'Пользователь не найден'})

        now = time.time()
        last = game.get('last_free_pours', 0)
        cooldown = 3600
        if now - last < cooldown:
            left = int(cooldown - (now - last))
            return web.json_response({'ok': False, 'error': 'Подожди', 'cooldown': left})

        game['pours'] = game.get('pours', 0) + 10
        game['last_free_pours'] = now
        save_data()
        return web.json_response({'ok': True, 'pours': game['pours'], 'reward': 10})

# ============ API КРЕСТИКОВ ============
async def ttt_result(request):
    data = await request.json()
    username = (data.get('username') or '').strip()
    result = (data.get('result') or '').strip()

    if not username:
        return web.json_response({'ok': False, 'error': 'Не указано имя'})
    if result not in ('win', 'lose', 'draw'):
        return web.json_response({'ok': False, 'error': 'Неверный результат'})

    async with lock:
        game = get_user_game_data(username)
        if game is None:
            return web.json_response({'ok': False, 'error': 'Пользователь не найден'})

        reward = 0
        if result == 'win':
            game['pours'] = game.get('pours', 0) + 30
            game['ttt_wins'] = game.get('ttt_wins', 0) + 1
            reward = 30
        elif result == 'lose':
            game['pours'] = max(0, game.get('pours', 0) - 15)
            game['ttt_losses'] = game.get('ttt_losses', 0) + 1
            reward = -15
        else:
            game['ttt_draws'] = game.get('ttt_draws', 0) + 1
            reward = 0

        save_data()
        return web.json_response({
            'ok': True, 'reward': reward,
            'pours': game['pours'],
            'wins': game.get('ttt_wins', 0),
            'losses': game.get('ttt_losses', 0),
            'draws': game.get('ttt_draws', 0)
        })

# ============ API ЗВОНКОВ ============
async def call_start(request):
    data = await request.json()
    caller = (data.get('from') or '').strip()
    recipient = (data.get('to') or '').strip()

    if not caller or not recipient or caller == recipient:
        return web.json_response({'ok': False, 'error': 'Неверные данные'})

    async with lock:
        active_calls[caller] = {'to': recipient, 'status': 'ringing', 'time': time.time()}
        save_data()
    return web.json_response({'ok': True})

async def call_accept(request):
    data = await request.json()
    caller = (data.get('caller') or '').strip()
    recipient = (data.get('recipient') or '').strip()

    async with lock:
        if caller in active_calls:
            active_calls[caller]['status'] = 'accepted'
            save_data()
            return web.json_response({'ok': True})
    return web.json_response({'ok': False, 'error': 'Звонок не найден'})

async def call_reject(request):
    data = await request.json()
    caller = (data.get('caller') or '').strip()

    async with lock:
        if caller in active_calls:
            del active_calls[caller]
            save_data()
            return web.json_response({'ok': True})
    return web.json_response({'ok': False})

async def call_end(request):
    data = await request.json()
    user = (data.get('user') or '').strip()

    async with lock:
        if user in active_calls:
            del active_calls[user]
        for caller, call in list(active_calls.items()):
            if call['to'] == user:
                del active_calls[caller]
        save_data()
    return web.json_response({'ok': True})

async def call_status(request):
    data = await request.json()
    caller = (data.get('caller') or '').strip()
    async with lock:
        if caller in active_calls:
            return web.json_response({'ok': True, 'status': active_calls[caller]['status']})
    return web.json_response({'ok': False, 'status': 'ended'})

# ============ API ИГРЫ (PouStrel) ============
async def buy_skin(request):
    data = await request.json()
    username = (data.get('username') or '').strip()
    skin_id = (data.get('skin_id') or '').strip()
    price = int(data.get('price', 0))
    if not username or not skin_id:
        return web.json_response({'ok': False, 'error': 'Не указаны данные'})
    async with lock:
        game = get_user_game_data(username)
        if game is None:
            return web.json_response({'ok': False, 'error': 'Не найден'})
        if skin_id in game.get('owned_skins', []):
            return web.json_response({'ok': False, 'error': 'Уже куплен'})
        if game.get('pours', 0) < price:
            return web.json_response({'ok': False, 'error': 'Недостаточно Pours'})
        game['pours'] -= price
        game['owned_skins'].append(skin_id)
        save_data()
        return web.json_response({'ok': True, 'pours': game['pours'], 'owned_skins': game['owned_skins']})

async def equip_skin(request):
    data = await request.json()
    username = (data.get('username') or '').strip()
    skin_id = (data.get('skin_id') or '').strip()
    async with lock:
        game = get_user_game_data(username)
        if game is None:
            return web.json_response({'ok': False, 'error': 'Не найден'})
        if skin_id not in game.get('owned_skins', []):
            return web.json_response({'ok': False, 'error': 'Скин не куплен'})
        game['equipped_skin'] = skin_id
        save_data()
        return web.json_response({'ok': True, 'equipped_skin': skin_id})

async def match_result(request):
    data = await request.json()
    username = (data.get('username') or '').strip()
    won = bool(data.get('won', False))
    kills = int(data.get('kills', 0))
    deaths = int(data.get('deaths', 0))
    async with lock:
        game = get_user_game_data(username)
        if game is None:
            return web.json_response({'ok': False, 'error': 'Не найден'})
        reward = 20 if won else 5
        game['pours'] = game.get('pours', 0) + reward
        save_data()
        return web.json_response({'ok': True, 'reward': reward, 'pours': game['pours']})

# ============ ЗАПУСК ============
async def main():
    load_data()
    ensure_admin_exists()
    banned_users.discard(ADMIN_NAME)
    save_data()

    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/index.html', index)
    app.router.add_get('/gamestrel.html', gamestrel)
    app.router.add_get('/tictactoe.html', tictactoe)
    app.router.add_post('/auth', auth)
    app.router.add_post('/send', send_message)
    app.router.add_get('/messages', get_messages)
    app.router.add_get('/history', get_history)
    # API профиля
    app.router.add_post('/api/get-profile', get_profile)
    # API подарков
    app.router.add_get('/api/gifts', get_gifts)
    app.router.add_post('/api/send-gift', send_gift)
    # API получить pours
    app.router.add_post('/api/claim-free', claim_free)
    # API крестиков
    app.router.add_post('/api/ttt-result', ttt_result)
    # API звонков
    app.router.add_post('/api/call/start', call_start)
    app.router.add_post('/api/call/accept', call_accept)
    app.router.add_post('/api/call/reject', call_reject)
    app.router.add_post('/api/call/end', call_end)
    app.router.add_post('/api/call/status', call_status)
    # API игры
    app.router.add_post('/api/buy-skin', buy_skin)
    app.router.add_post('/api/equip-skin', equip_skin)
    app.router.add_post('/api/match-result', match_result)

    port = int(os.environ.get('PORT', 8080))
    print(f'[POU] Запуск на порту {port}')
    print(f'[POU] Админ: {ADMIN_NAME}')
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
                expired_mutes = [u for u, t in muted_users.items() if t != 0 and now > t]
                for u in expired_mutes:
                    del muted_users[u]
                # Чистим старые звонки
                old_calls = [c for c, v in active_calls.items() if now - v['time'] > 60]
                for c in old_calls:
                    del active_calls[c]
    asyncio.create_task(cleanup())

    async def autosave():
        while True:
            await asyncio.sleep(30)
            async with lock:
                save_data()
    asyncio.create_task(autosave())

    await asyncio.Future()

if __name__ == '__main__':
    asyncio.run(main())
