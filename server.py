import asyncio
import json
import os
from datetime import datetime
import websockets
from websockets.http11 import Response

clients = set()
lock = asyncio.Lock()

HTML_FILE = os.path.join(os.path.dirname(__file__), 'static', 'index.html')

async def process_request(connection, request):
    if request.path in ('/', '/index.html'):
        try:
            with open(HTML_FILE, 'rb') as f:
                body = f.read()
            return Response(200, 'OK', websockets.Headers([
                ('Content-Type', 'text/html; charset=utf-8'),
                ('Content-Length', str(len(body))),
            ]), body)
        except Exception as e:
            return Response(500, 'Error', websockets.Headers([
                ('Content-Type', 'text/plain'),
            ]), str(e).encode())

async def handler(websocket):
    async with lock:
        clients.add(websocket)
    try:
        async for message in websocket:
            data = json.loads(message)
            data['time'] = datetime.now().strftime('%H:%M:%S')
            async with lock:
                for client in list(clients):
                    try:
                        await client.send(json.dumps(data, ensure_ascii=False))
                    except Exception:
                        pass
    finally:
        async with lock:
            clients.discard(websocket)

async def main():
    port = int(os.environ.get('PORT', 10000))
    print(f'[POU] Запуск на порту {port}')
    async with websockets.serve(handler, '0.0.0.0', port, process_request=process_request):
        await asyncio.Future()

if __name__ == '__main__':
    asyncio.run(main())
