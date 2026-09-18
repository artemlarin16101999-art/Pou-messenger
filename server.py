from aiohttp import web
import json
import os
from datetime import datetime
import asyncio

clients = set()

async def index(request):
    path = os.path.join(os.path.dirname(__file__), 'static', 'index.html')
    return web.FileResponse(path)

async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    clients.add(ws)
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                data = json.loads(msg.data)
                data['time'] = datetime.now().strftime('%H:%M:%S')
                text = json.dumps(data, ensure_ascii=False)
                for client in list(clients):
                    if not client.closed:
                        try:
                            await client.send_str(text)
                        except Exception:
                            pass
    finally:
        clients.discard(ws)
    return ws

async def main():
    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/index.html', index)
    app.router.add_get('/ws', websocket_handler)
    port = int(os.environ.get('PORT', 8080))
    print(f'[POU] Запуск на порту {port}')
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print('[POU] Сервер готов')
    await asyncio.Future()

if __name__ == '__main__':
    asyncio.run(main())
