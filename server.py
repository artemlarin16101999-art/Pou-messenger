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
    app.router.add_get('/api/garden/chat/get', garden_chat_
                       uter.add_post('/api/garden/admin/give', garden_admin_give)
    app.router.add_post('/api/garden/admin/list', garden
    port = int(os.environ.get('PORT', 8080))[GARDEN] Админ: {GARDEN_ADMIN}')
    runner 
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    prin
    async def cleanup():
        while True:
            await asyncio.sleep(10)
            async with l [n for n, p in garden_players.items() if now - p['time'] > GARDEN_TIMEOUT]
                for n in exarden_players[n]

  t asyncio.sleep(30)
            async with lock:
                save_garden()

    asyncio.create_task(cleanup())
    asyncio.create_task(autosave())
    await asyncio.Future()

if __name__ == '__main__':
    asyncio.run(main())
