from aiohttp import web
import json
import os
import time
import asyncio
from datetime import datetime

messages = []
users_online = {}
lock = asyncio.Lock()

ONLINE_TIMEOUT = 30
MAX_HISTORY = 500

async def index(request):
    path = os.path.join(os.path.dirname(__file__), 'static', 'index.html')
    return web.FileResponse(path)

async def send_message(request):
    data = await request.json()
    async with lock:
        msg = {
            'from': data.get('from', 'Guest'),
            'text': data.get('text', ''),
            'time': datetime.now().strftime('%H:%M:%S'),
            'timestamp': time.time(),
            'to': data.get('to')  # None = общий, "Bob" = личное Bob'у
        }
        messages.append(msg)
        if len(messages) > MAX_HISTORY:
            messages[:] = messages[-MAX_HISTORY:]
        users_online[msg['from']] = time.time()
    return web.json_response({'ok': True})

async def get_messages(request):
    try:
        since = float(request.query.get('since', 0))
    except ValueError:
        since = 0
    user = request.query.get('user', '')

    async with lock:
        if user:
            users_online[user] = time.time()

        now = time.time()
        expired = [u for u, t in users_online.items() if now - t > ONLINE_TIMEOUT]
        for u in expired:
            del users_online[u]

        new_msgs = []
        for m in messages:
            if m['timestamp'] <= since:
                continue
            # Общий чат — все
            if m.get('to') is None:
                new_msgs.append(m)
            # Личное — только адресованные нам или от нас
            elif m['to'] == user or m['from'] == user:
                new_msgs.append(m)

        return web.json_response({
            'messages': new_msgs,
            'online': list(users_online.keys()),
            'now': now
        })

async def get_history(request):
    """История всех сообщений (клиент сам разложит по чатам)"""
    async with lock:
        return web.json_response({'messages': messages[-300:]})

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
